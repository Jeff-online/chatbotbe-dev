import os
import uuid
import logging
import json
import time
import random
from datetime import datetime, timedelta, timezone
from app import messages
from . import system_api
from .args_parser import TaskQueuePostParser, TaskQueueDeleteParser, QueueStateGetParser, QueueStatePostParser, TaskQueueGetParser, TaskQueuePutParser
from flask import current_app
from common.common_resource import GlobalResource
from azure.storage.queue import QueueClient
from azure.core.exceptions import ResourceExistsError
from azure.cosmos.exceptions import CosmosHttpResponseError
from utils.file_utils import cal_tokens
from azure.cosmos import CosmosClient

logger = logging.getLogger(__name__)


class QueueConcurrencyLock:
    """队列并发锁控制类，参考 call_openai_with_global_lock_gpt5 实现"""
    
    LOCK_CONTAINER_NAME = "queue_concurrency_lock"
    HEAVY_LOCK_ID = "heavy_queue_lock"
    LIGHT_LOCK_ID = "light_queue_lock"
    MAX_HEAVY_TASKS = 1
    MAX_LIGHT_TASKS = 2
    RETRY_INTERVAL_SECONDS = 3
    PROCESSING_TIMEOUT_MINUTES = 10
    
    @staticmethod
    def get_lock_container():
        """获取锁容器"""
        if not hasattr(current_app, 'cosmos_client') or current_app.cosmos_client is None:
            logger.error("❌ Cosmos client not initialized in current_app")
            raise Exception("Cosmos DB client not initialized")
        
        client = current_app.cosmos_client
        database = client.get_database_client(current_app.config['DATABASE_NAME'])
        
        try:
            lock_container = database.create_container_if_not_exists(
                id=QueueConcurrencyLock.LOCK_CONTAINER_NAME,
                partition_key={"path": "/id"},
                offer_throughput=400
            )
            return lock_container
        except Exception as e:
            logger.error(f"❌ Failed to create lock container: {e}")
            raise
    
    @staticmethod
    def acquire_lock(queue_name: str, message_id: str, session_id: str = None) -> bool:
        """
        获取队列处理锁
        :param queue_name: 队列名称 (heavy-queue 或 light-queue)
        :param message_id: 消息 ID
        :param session_id: 会话 ID
        :return: 是否成功获取锁
        """
        if queue_name == "heavy-queue":
            lock_id = QueueConcurrencyLock.HEAVY_LOCK_ID
            max_tasks = QueueConcurrencyLock.MAX_HEAVY_TASKS
        elif queue_name == "light-queue":
            lock_id = QueueConcurrencyLock.LIGHT_LOCK_ID
            max_tasks = QueueConcurrencyLock.MAX_LIGHT_TASKS
        else:
            logger.error(f"❌ Unknown queue name: {queue_name}")
            return False
        
        lock_container = QueueConcurrencyLock.get_lock_container()
        lock_acquired = False
        
        # 循环尝试获取锁
        while not lock_acquired:
            try:
                # 读取锁文档
                lock_doc = lock_container.read_item(item=lock_id, partition_key=lock_id)
                
                # 检查是否有超时锁需要释放
                current_time = datetime.now(timezone.utc)
                processing_slots = lock_doc.get('processing_slots', [])
                
                # 清理超时的槽位
                cleaned_slots = []
                for slot in processing_slots:
                    locked_at_str = slot.get('locked_at')
                    if locked_at_str:
                        try:
                            locked_at_time = datetime.fromisoformat(locked_at_str.replace('Z', '+00:00'))
                            timeout_threshold = current_time - timedelta(minutes=QueueConcurrencyLock.PROCESSING_TIMEOUT_MINUTES)
                            
                            if locked_at_time < timeout_threshold:
                                logger.warning(f"⚠️ 检测到{queue_name}锁槽位超时，强制释放：{slot.get('message_id')}")
                            else:
                                cleaned_slots.append(slot)
                        except Exception as parse_err:
                            logger.error(f"❌ 时间解析失败：{parse_err}")
                            cleaned_slots.append(slot)
                    else:
                        cleaned_slots.append(slot)
                
                lock_doc['processing_slots'] = cleaned_slots
                
                # 检查是否有可用槽位
                active_count = len(cleaned_slots)
                
                if active_count < max_tasks:
                    # 有空闲槽位，尝试占用
                    new_slot = {
                        'message_id': message_id,
                        'session_id': session_id,
                        'locked_at': current_time.isoformat(),
                        'occupied_by': os.environ.get("ACCOUNT_URL", "unknown")
                    }
                    lock_doc['processing_slots'].append(new_slot)
                    
                    try:
                        lock_container.replace_item(item=lock_doc, body=lock_doc)
                        logger.info(f"✅ 成功获取{queue_name}锁槽位，当前活跃数：{len(lock_doc['processing_slots'])}/{max_tasks}")
                        lock_acquired = True
                        return True
                    except CosmosHttpResponseError as e:
                        if e.status_code == 412:
                            logger.warning(f"⚠️ 获取{queue_name}锁时发生并发冲突，将在 {QueueConcurrencyLock.RETRY_INTERVAL_SECONDS} 秒后重试...")
                            time.sleep(QueueConcurrencyLock.RETRY_INTERVAL_SECONDS + random.uniform(0, 0.5))
                            continue
                        else:
                            raise
                else:
                    # 没有空闲槽位，需要排队等待
                    logger.info(f"⏳ {queue_name}已达最大并发数 ({active_count}/{max_tasks})，任务 {message_id} 进入等待...")
                    time.sleep(QueueConcurrencyLock.RETRY_INTERVAL_SECONDS)
                    
            except CosmosHttpResponseError as e:
                if e.status_code == 404:
                    # 锁文档不存在，创建初始文档
                    logger.info(f"🔵 锁文档 {lock_id} 不存在，正在初始化...")
                    new_lock_doc = {
                        "id": lock_id,
                        "queue_type": queue_name,
                        "max_concurrent_tasks": max_tasks,
                        "processing_slots": [],
                        "created_at": datetime.now(timezone.utc).isoformat()
                    }
                    try:
                        lock_container.create_item(body=new_lock_doc)
                        continue
                    except Exception as create_error:
                        logger.error(f"❌ 初始化锁文档失败：{create_error}")
                        raise
                elif e.status_code == 412:
                    logger.warning(f"⚠️ 读取锁时发生并发冲突，立即重试...")
                    time.sleep(random.uniform(0.1, 0.5))
                    continue
                else:
                    logger.error(f"❌ 数据库操作失败，状态码：{e.status_code}")
                    raise
            except Exception as e:
                logger.error(f"❌ 获取锁时发生未知错误：{e}")
                time.sleep(QueueConcurrencyLock.RETRY_INTERVAL_SECONDS)
                continue
        
        return lock_acquired
    
    @staticmethod
    def release_lock(queue_name: str, message_id: str) -> bool:
        """
        释放队列处理锁
        :param queue_name: 队列名称
        :param message_id: 消息 ID
        :return: 是否成功释放锁
        """
        if queue_name == "heavy-queue":
            lock_id = QueueConcurrencyLock.HEAVY_LOCK_ID
        elif queue_name == "light-queue":
            lock_id = QueueConcurrencyLock.LIGHT_LOCK_ID
        else:
            logger.error(f"❌ Unknown queue name: {queue_name}")
            return False
        
        try:
            lock_container = QueueConcurrencyLock.get_lock_container()
            lock_doc = lock_container.read_item(item=lock_id, partition_key=lock_id)
            
            # 找到并移除对应的槽位
            processing_slots = lock_doc.get('processing_slots', [])
            original_count = len(processing_slots)
            
            lock_doc['processing_slots'] = [
                slot for slot in processing_slots 
                if slot.get('message_id') != message_id
            ]
            
            if len(lock_doc['processing_slots']) < original_count:
                lock_container.replace_item(item=lock_doc, body=lock_doc)
                logger.info(f"✅ 成功释放{queue_name}锁槽位，当前活跃数：{len(lock_doc['processing_slots'])}")
                return True
            else:
                logger.warning(f"⚠️ 未找到要释放的锁槽位，message_id: {message_id}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 释放锁失败：{e}")
            return False


class QueueState(GlobalResource):

    @staticmethod
    def create(username: str, queue_name: str, message: str, message_id: str, status: str, account_name: str = None, session_id: str = None, pop_receipt: str = None) -> str:
        """
        创建队列状态记录
        """
        logger.info(f"🔵 QueueState.create started for user: {username}, message_id: {message_id}")
        
        try:
            doc_id = str(uuid.uuid4())
            item = {
                "id": doc_id,
                "type": "queue_state",
                "username": username,
                "queue_name": queue_name,
                "message": message,
                "message_id": message_id,
                "pop_receipt": pop_receipt,
                "status": status,
                "account_name": account_name,
                "create_time": datetime.now().isoformat()
            }
            if session_id:
                item["session_id"] = session_id
            
            if not hasattr(current_app, 'container_task_queue') or current_app.container_task_queue is None:
                logger.error("❌ current_app.container_task_queue is NOT initialized!")
                raise Exception("Cosmos DB container not initialized in current_app")
            
            # 直接尝试创建 item，不再进行冗余的 container.read() 检查
            result = current_app.container_task_queue.create_item(body=item)
            logger.info(f"✅ Queue state item created successfully in database, id: {result.get('id')}")
            return doc_id
            
        except Exception as e:
            logger.error(f"❌ Error in QueueState.create: {e}", exc_info=True)
            raise

    @staticmethod
    def update_status_by_message_id(message_id: str, status: str) -> None:
        """
        根据 message_id 更新队列状态
        :param message_id: 消息 ID
        :param status: 新状态（queued, processing, completed, failed, parsed）
        """
        query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.message_id = @message_id"
        params = [{"name": "@message_id", "value": message_id}]
        items = list(current_app.container_task_queue.query_items(query=query, parameters=params, enable_cross_partition_query=True))
        for item in items:
            item["status"] = status
            item["update_time"] = datetime.now().isoformat()
            current_app.container_task_queue.upsert_item(item)
        logger.info(f"✅ Updated queue state to '{status}' for message_id: {message_id}")

    @staticmethod
    def delete_by_message_id(message_id: str) -> None:
        query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.message_id = @message_id"
        params = [{"name": "@message_id", "value": message_id}]
        items = list(current_app.container_task_queue.query_items(query=query, parameters=params, enable_cross_partition_query=True))
        for item in items:
            doc_id = item.get("id")
            if doc_id:
                current_app.container_task_queue.delete_item(item=doc_id, partition_key=doc_id)

    @staticmethod
    def delete_by_filename(username: str, filename: str) -> None:
        """根据用户名和文件名删除所有状态的队列记录和消息"""
        logger.info(f"🔵 Attempting to delete queue record and message for user: {username}, file: {filename}")
        
        # 查询所有状态的记录（包括 uploaded, queued, processing, parsed, failed）
        query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username"
        params = [{"name": "@username", "value": username}]
        
        try:
            items = list(current_app.container_task_queue.query_items(query=query, parameters=params, enable_cross_partition_query=True))
            logger.info(f"🔍 Found {len(items)} records (all statuses) for user {username}")
            
            deleted_count = 0
            for item in items:
                message_data = item.get("message", {})
                if isinstance(message_data, str):
                    try:
                        message_data = json.loads(message_data)
                    except Exception as e:
                        logger.error(f"❌ Failed to parse message JSON in record {item.get('id')}: {e}")
                        continue
                
                attachments = message_data.get("attachment_names", [])
                logger.info(f"   Checking record {item.get('id')} (status: {item.get('status')}), attachments: {attachments}")
                
                if filename in attachments:
                    # 找到匹配的记录
                    message_id = item.get("message_id")
                    pop_receipt = item.get("pop_receipt")
                    queue_name = item.get("queue_name")
                    status = item.get("status")
                    
                    # 从 Azure Queue 中删除消息（仅对 queued 状态）
                    if status == 'queued' and message_id and pop_receipt and queue_name:
                        try:
                            connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
                            if connection_string:
                                from azure.storage.queue import QueueClient
                                queue_client = QueueClient.from_connection_string(connection_string, queue_name)
                                queue_client.delete_message(message_id, pop_receipt)
                                logger.info(f"✅ Deleted message {message_id} from queue {queue_name} for file {filename}")
                            else:
                                logger.warning(f"⚠️ AZURE_STORAGE_CONNECTION_STRING not set, skipping queue deletion")
                        except Exception as e:
                            logger.warning(f"⚠️ Failed to delete message from queue: {e}")
                    
                    # 从 Cosmos DB 中删除记录（所有状态）
                    doc_id = item.get("id")
                    if doc_id:
                        current_app.container_task_queue.delete_item(item=doc_id, partition_key=doc_id)
                        logger.info(f"✅ Deleted queue state record {doc_id} (status: {status}) for file {filename}")
                        deleted_count += 1
            
            if deleted_count > 0:
                logger.info(f"✅ Successfully deleted {deleted_count} record(s) for file {filename}")
            else:
                logger.warning(f"⚠️ No records found for file {filename}")
                
        except Exception as e:
            logger.error(f"❌ Error during delete_by_filename: {e}", exc_info=True)
            raise e

    def get(self):
        args_parser = QueueStateGetParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        queue_name = args.get("queue_name")
        message_id = args.get("message_id")
        status = args.get("status")
        query = "SELECT * FROM c WHERE c.type = 'queue_state'"
        params = []
        if username:
            query += " AND c.username = @username"
            params.append({"name": "@username", "value": username})
        if queue_name:
            query += " AND c.queue_name = @queue_name"
            params.append({"name": "@queue_name", "value": queue_name})
        if message_id:
            query += " AND c.message_id = @message_id"
            params.append({"name": "@message_id", "value": message_id})
        if status:
            query += " AND c.status = @status"
            params.append({"name": "@status", "value": status})
        items = list(current_app.container_task_queue.query_items(query=query, parameters=params, enable_cross_partition_query=True))
        result = []
        light_queue_count = 0
        heavy_queue_count = 0
        for item in items:
            q_name = item.get("queue_name", "")
            if q_name == "light-queue":
                light_queue_count += 1
            elif q_name == "heavy-queue":
                heavy_queue_count += 1
            result.append({
                "id": item.get("id", ""),
                "username": item.get("username", ""),
                "queue_name": q_name,
                "message": item.get("message", ""),
                "message_id": item.get("message_id", ""),
                "status": item.get("status", ""),
                "create_time": item.get("create_time", ""),
                "update_time": item.get("update_time", "")
            })
        return {
            "total_count": len(result),
            "light_queue_count": light_queue_count,
            "heavy_queue_count": heavy_queue_count,
            "queue_state": result,
            "code": 200,
            "params": params
        }

    def post(self):
        args_parser = QueueStatePostParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        queue_name = args.get("queue_name")
        message = args.get("message")
        message_id = args.get("message_id")
        pop_receipt = args.get("pop_receipt")
        status = args.get("status")
        session_id = args.get("session_id")
        doc_id = QueueState.create(
            username=username,
            queue_name=queue_name,
            message=message,
            message_id=message_id,
            pop_receipt=pop_receipt,
            status=status,
            session_id=session_id
        )
        return {"id": doc_id, "code": 200}


class QueueStats(GlobalResource):
    
    def get(self):
        """获取队列统计信息（包含待处理和已解析）"""
        args_parser = QueueStateGetParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        
        # 查询 queued 和 processing 状态（待处理）
        query_pending = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.status IN ('queued', 'processing')"
        params = []
        
        if username:
            query_pending += " AND c.username = @username"
            params.append({"name": "@username", "value": username})
        
        items_pending = list(current_app.container_task_queue.query_items(
            query=query_pending, 
            parameters=params, 
            enable_cross_partition_query=True
        ))
        
        # 查询 parsed 状态（已解析完成）
        query_parsed = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.status = 'parsed'"
        params_parsed = []
        
        if username:
            query_parsed += " AND c.username = @username"
            params_parsed.append({"name": "@username", "value": username})
        
        items_parsed = list(current_app.container_task_queue.query_items(
            query=query_parsed,
            parameters=params_parsed,
            enable_cross_partition_query=True
        ))
        
        # 统计待处理（包含详细信息用于计算排队位置）
        total_pending = 0
        light_queue_pending = 0
        heavy_queue_pending = 0
        light_attachment_names = []
        heavy_attachment_names = []
        
        # 新增：按 create_time 排序，用于计算排队位置
        pending_tasks = []
        
        for item in items_pending:
            q_name = item.get("queue_name", "")
            message_data = item.get("message", {})
            
            # Parse message JSON if it's a string
            if isinstance(message_data, str):
                try:
                    message_data = json.loads(message_data)
                except:
                    pass
            
            # Extract attachment names from message
            attachment_names = message_data.get("attachment_names", []) if isinstance(message_data, dict) else []
            session_id = message_data.get("session_id") or item.get("session_id")
            
            total_pending += 1
            if q_name == "light-queue":
                light_queue_pending += 1
                if attachment_names:
                    light_attachment_names.extend(attachment_names)
            elif q_name == "heavy-queue":
                heavy_queue_pending += 1
                if attachment_names:
                    heavy_attachment_names.extend(attachment_names)
            
            # 记录任务信息（用于计算排队位置）
            pending_tasks.append({
                "id": item.get("id"),
                "queue_name": q_name,
                "status": item.get("status"),
                "attachment_names": attachment_names,
                "session_id": session_id,
                "create_time": item.get("create_time"),
                "update_time": item.get("update_time")
            })
        
        # 统计已解析
        total_parsed = len(items_parsed)
        parsed_attachment_names = []
        for item in items_parsed:
            message_data = item.get("message", {})
            if isinstance(message_data, str):
                try:
                    message_data = json.loads(message_data)
                except:
                    pass
            attachment_names = message_data.get("attachment_names", []) if isinstance(message_data, dict) else []
            if attachment_names:
                parsed_attachment_names.extend(attachment_names)
        
        # 新增：计算排队位置（排在前面的任务数量）
        # 按 create_time 排序所有待处理任务
        sorted_tasks = sorted(pending_tasks, key=lambda x: x.get('create_time', ''))
        
        # 默认返回 0（表示没有任务排在前面）
        queue_position = 0
        
        # 如果有 pending_tasks，返回第一个任务的索引位置（即前面的任务数量）
        # 注意：这里假设前端会传递 session_id 或 attachment_names 来识别自己的任务
        # 为了简化，我们直接返回总任务数 - 1（假设最新的任务是当前用户的）
        if len(sorted_tasks) > 0:
            # 找到最新的任务（假设是当前用户上传的）
            # 实际上应该通过 session_id 或 attachment_names 来匹配
            # 但为了性能，这里简化处理：返回总任务数 - 1
            queue_position = len(sorted_tasks) - 1
        
        return {
            "total_pending": total_pending,
            "light_queue_pending": light_queue_pending,
            "heavy_queue_pending": heavy_queue_pending,
            "light_attachment_names": light_attachment_names,
            "heavy_attachment_names": heavy_attachment_names,
            "total_parsed": total_parsed,
            "parsed_attachment_names": parsed_attachment_names,
            "queue_position": queue_position,  # 新增：排在前面的任务数量
            "pending_tasks": pending_tasks,  # 保留：详细任务列表（可选）
            "code": 200
        }


class TaskQueue(GlobalResource):
    HEAVY_QUEUE_THRESHOLD = 50000

    @staticmethod
    def _get_queue_client(queue_name: str) -> QueueClient:
        connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not connection_string:
            raise messages.InterfaceCallError("AZURE_STORAGE_CONNECTION_STRING not configured")
        return QueueClient.from_connection_string(connection_string, queue_name)

    def post(self):
        args_parser = TaskQueuePostParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        queue_name = args.get("queue_name")
        message_content = args.get("message")
        account_name = args.get("account_name")
        attachment_names = args.get("attachment_names")
        session_id = args.get("session_id")

        if not username:
            raise messages.UserNameNotExistsError

        # Determine queue based on tokens if attachment_names provided
        if attachment_names:
            token_result = cal_tokens(username, attachment_names)
            total_tokens = token_result.get("total_tokens", 0)
            if total_tokens > self.HEAVY_QUEUE_THRESHOLD:
                queue_name = "heavy-queue"
            else:
                queue_name = "light-queue"
        elif not queue_name:
            queue_name = "light-queue"

        create_time = datetime.now().isoformat()
        status = "queued"

        # Construct message payload
        message_payload = {
            "account_name": account_name,
            "queue_name": queue_name,
            "user-name": username,
            "create_time": create_time,
            "status": status,
            "message": message_content,
            "attachment_names": attachment_names if attachment_names else [],
            "session_id": session_id
        }
        message_json = json.dumps(message_payload)

        try:
            queue_client = self._get_queue_client(queue_name)
            try:
                queue_client.create_queue()
            except ResourceExistsError:
                pass
            
            send_result = queue_client.send_message(message_json)
            queue_state_id = QueueState.create(
                username=username,
                queue_name=queue_name,
                message=message_json,
                message_id=send_result.id,
                pop_receipt=send_result.pop_receipt,
                status=status,
                account_name=account_name,
                session_id=session_id
            )
            logger.info(f"✅ Created queue record {queue_state_id} for user {username}")
            logger.info(f"user: {username} send message to queue: {queue_name}")
            return {
                "message_id": send_result.id,
                "pop_receipt": send_result.pop_receipt,
                "insertion_time": str(send_result.inserted_on) if getattr(send_result, "inserted_on", None) else None,
                "queue_state_id": queue_state_id,
                "queue_name": queue_name,
                "code": 200
            }
        except Exception as e:
            logger.error(f"Failed to send message to queue {queue_name}: {e}")
            return {"msg": str(e), "code": 500}

    def get(self):
        args_parser = TaskQueueGetParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        queue_name = args.get("queue_name")
        max_messages = args.get("max_messages", 1)

        if not username:
            raise messages.UserNameNotExistsError

        try:
            queue_client = self._get_queue_client(queue_name)
            messages_received = queue_client.receive_messages(messages_per_page=max_messages)
            
            result = []
            for msg in messages_received:
                try:
                    content = json.loads(msg.content)
                except:
                    content = msg.content
                
                result.append({
                    "message_id": msg.id,
                    "pop_receipt": msg.pop_receipt,
                    "content": content,
                    "insertion_time": str(msg.inserted_on) if getattr(msg, "inserted_on", None) else None,
                    "dequeue_count": msg.dequeue_count
                })
            
            return {"messages": result, "count": len(result), "code": 200}
        except Exception as e:
            logger.error(f"Failed to receive messages from queue {queue_name}: {e}")
            return {"msg": str(e), "code": 500}

    @staticmethod
    def process_with_lock(queue_name: str, message_id: str, processor_func, *args, **kwargs):
        """
        带锁的任务处理方法，确保同一时间只有指定数量的任务在处理
        :param queue_name: 队列名称 (heavy-queue 或 light-queue)
        :param message_id: 消息 ID
        :param processor_func: 实际的处理函数
        :param args: 处理函数的参数
        :param kwargs: 处理函数的关键字参数
        :return: 处理结果
        """
        lock_acquired = False
        
        try:
            # 1. 获取锁（会阻塞直到获取到锁）
            session_id = kwargs.get('session_id')
            logger.info(f"🔵 开始为任务 {message_id} 获取{queue_name}锁 (session_id={session_id})...")
            lock_acquired = QueueConcurrencyLock.acquire_lock(queue_name, message_id, session_id=session_id)
            
            if not lock_acquired:
                logger.error(f"❌ 任务 {message_id} 获取锁失败")
                raise Exception("Failed to acquire concurrency lock")
            
            logger.info(f"✅ 任务 {message_id} 成功获取锁，开始处理...")
            
            # 2. 更新状态为 processing
            QueueState.update_status_by_message_id(message_id, "processing")
            
            # 3. 执行实际的处理逻辑
            result = processor_func(*args, **kwargs)
            
            # 4. 更新状态为 parsed (表示文件已解析完成，AI 已返回响应)
            # 注意：使用 'parsed' 而不是 'completed'，因为这是 session 级别的文件解析状态
            QueueState.update_status_by_message_id(message_id, "parsed")
            
            logger.info(f"✅ 任务 {message_id} 处理完成，状态已更新为 'parsed'")
            return result
            
        except Exception as e:
            logger.error(f"❌ 任务 {message_id} 处理失败：{e}", exc_info=True)
            QueueState.update_status_by_message_id(message_id, "failed")
            raise e
            
        finally:
            # 5. 释放锁（无论成功或失败）
            if lock_acquired:
                try:
                    QueueConcurrencyLock.release_lock(queue_name, message_id)
                    logger.info(f"✅ 任务 {message_id} 已释放锁")
                except Exception as release_error:
                    logger.error(f"❌ 释放锁失败：{release_error}")

    def put(self):
        args_parser = TaskQueuePutParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        queue_name = args.get("queue_name")
        message_id = args.get("message_id")
        pop_receipt = args.get("pop_receipt")
        message_content = args.get("message")
        visibility_timeout = args.get("visibility_timeout", 0)

        if not username:
            raise messages.UserNameNotExistsError

        try:
            queue_client = self._get_queue_client(queue_name)
            # Fetch current message to update its content but maintain other fields if needed
            # Actually Azure Queue update_message replaces the content
            # We should probably maintain the structure
            
            # Since we don't easily have the original message without receiving it, 
            # we assume the update message_content is the full new content or we just update the 'message' field in JSON.
            # But update_message is usually used for extending visibility timeout or small content updates.
            
            # Let's assume the user wants to update the 'message' part of the JSON.
            # This is tricky without knowing the old content. 
            # If the user provides a string, we'll wrap it in the JSON structure if it looks like it's meant to be that.
            
            # For simplicity, we'll just send the new message_content. 
            # If it's not JSON, we'll wrap it.
            
            try:
                # Try to see if it's already a full payload
                json_payload = json.loads(message_content)
                if not all(k in json_payload for k in ["user-name", "status"]):
                    raise ValueError
                message_json = message_content
            except:
                # Wrap it
                message_payload = {
                    "user-name": username,
                    "queue_name": queue_name,
                    "status": "updated",
                    "create_time": datetime.now().isoformat(),
                    "message": message_content
                }
                message_json = json.dumps(message_payload)

            update_result = queue_client.update_message(
                message_id, 
                pop_receipt, 
                content=message_json, 
                visibility_timeout=visibility_timeout
            )
            
            QueueState.update_status_by_message_id(message_id, "updated")
            
            return {
                "message_id": message_id,
                "pop_receipt": update_result.pop_receipt,
                "next_visible_on": str(update_result.next_visible_on) if getattr(update_result, "next_visible_on", None) else None,
                "code": 200
            }
        except Exception as e:
            logger.error(f"Failed to update message in queue {queue_name}: {e}")
            return {"msg": str(e), "code": 500}

    def delete(self):
        args_parser = TaskQueueDeleteParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        queue_name = args.get("queue_name")
        message_id = args.get("message_id")
        pop_receipt = args.get("pop_receipt")

        if not username:
            raise messages.UserNameNotExistsError

        try:
            queue_client = self._get_queue_client(queue_name)
            queue_client.delete_message(message_id, pop_receipt)
            QueueState.delete_by_message_id(message_id)
            logger.info(f"user: {username} delete message from queue: {queue_name}")
            return {"msg": "success", "code": 200}
        except Exception as e:
            logger.error(f"Failed to delete message from queue {queue_name}: {e}")
            return {"msg": str(e), "code": 500}


class DeleteUploadedRecord(GlobalResource):
    """
    删除已上传但未提交的队列记录
    当用户在前端取消文件上传时调用此接口
    """
    
    def delete(self):
        """
        根据文件名删除 uploaded 状态的记录
        """
        try:
            import json
            from flask import request
            
            data = request.get_json()
            username = data.get('username')
            filename = data.get('filename')
            
            if not username:
                raise messages.UserNameNotExistsError
            
            if not filename:
                return {
                    'message': 'Filename is required',
                    'code': 400
                }
            
            logger.info(f"🔵 DeleteUploadedRecord: user={username}, file={filename}")
            
            # 只删除 uploaded 状态的记录
            query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username AND c.status = 'uploaded'"
            params = [{"name": "@username", "value": username}]
            
            items = list(current_app.container_task_queue.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True
            ))
            
            deleted_count = 0
            for item in items:
                message_data = item.get('message', {})
                if isinstance(message_data, str):
                    try:
                        message_data = json.loads(message_data)
                    except:
                        pass
                
                file_attachments = message_data.get('attachment_names', []) if isinstance(message_data, dict) else []
                
                if filename in file_attachments:
                    # 删除记录
                    doc_id = item.get('id')
                    if doc_id:
                        current_app.container_task_queue.delete_item(item=doc_id, partition_key=doc_id)
                        logger.info(f"✅ Deleted uploaded record {doc_id} for file {filename}")
                        deleted_count += 1
            
            if deleted_count > 0:
                return {
                    'message': f'Deleted {deleted_count} uploaded record(s) for file {filename}',
                    'code': 200,
                    'deleted_count': deleted_count
                }
            else:
                return {
                    'message': f'No uploaded records found for file {filename}',
                    'code': 200,
                    'deleted_count': 0
                }
                
        except Exception as e:
            logger.error(f"❌ DeleteUploadedRecord error: {e}", exc_info=True)
            return {'msg': str(e), 'code': 500}


class SubmitQueuedTasks(GlobalResource):
    """
    提交已上传的文件到队列进行处理
    当用户点击"送信"按钮时调用此接口
    """
    
    def post(self):
        """
        提交待处理任务到队列
        前端应传递 attachment_names 列表，后端会找到对应的 uploaded 状态记录并转换为 queued 状态
        """
        try:
            import json
            from flask import request
            
            data = request.get_json()
            username = data.get('username')
            session_id = data.get('session_id')
            attachment_names = data.get('attachment_names', [])
            
            if not username:
                raise messages.UserNameNotExistsError
            
            if not attachment_names:
                return {
                    'message': 'No attachments provided',
                    'code': 200,
                    'submitted_count': 0
                }
            
            logger.info(f"🔵 SubmitQueuedTasks: user={username}, attachments={attachment_names}")
            
            # 查找所有 status='uploaded' 的记录
            query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username AND c.status = 'uploaded'"
            params = [{"name": "@username", "value": username}]
            items = list(current_app.container_task_queue.query_items(
                query=query, 
                parameters=params, 
                enable_cross_partition_query=True
            ))
            
            submitted_count = 0
            results = []
            
            for item in items:
                message_data = item.get('message', {})
                if isinstance(message_data, str):
                    try:
                        message_data = json.loads(message_data)
                    except:
                        pass
                
                file_attachments = message_data.get('attachment_names', []) if isinstance(message_data, dict) else []
                
                # 检查这个记录是否在要提交的附件列表中
                should_submit = False
                for filename in file_attachments:
                    if filename in attachment_names:
                        should_submit = True
                        break
                
                if should_submit:
                    try:
                        # 更新记录状态为 'queued'
                        item['status'] = 'queued'
                        item['update_time'] = datetime.now().isoformat()
                        
                        # 更新 message 内容
                        message_data['status'] = 'queued'
                        message_data['message'] = message_data.get('message', '').replace('(waiting for submit)', '')
                        item['message'] = json.dumps(message_data)
                        
                        current_app.container_task_queue.upsert_item(item)
                        
                        logger.info(f"✅ Submitted task for file: {file_attachments}, queue: {item.get('queue_name')}")
                        
                        # 实际发送到 Azure Queue
                        queue_name = item.get('queue_name', 'light-queue')
                        connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
                        
                        if connection_string:
                            from azure.storage.queue import QueueClient
                            from azure.core.exceptions import ResourceExistsError
                            queue_client = QueueClient.from_connection_string(connection_string, queue_name)
                            try:
                                queue_client.create_queue()
                            except ResourceExistsError:
                                pass
                            
                            send_result = queue_client.send_message(item['message'])
                            logger.info(f"✅ Sent to queue {queue_name}, message_id: {send_result.id}")
                            
                            # 更新 DB 记录，添加真实的 message_id 和 pop_receipt
                            item['message_id'] = send_result.id
                            item['pop_receipt'] = send_result.pop_receipt
                            item['status'] = 'queued'
                            current_app.container_task_queue.upsert_item(item)
                        
                        submitted_count += 1
                        results.append({
                            'filename': file_attachments,
                            'queue_name': queue_name,
                            'status': 'queued'
                        })
                        
                    except Exception as submit_err:
                        logger.error(f"❌ Failed to submit task: {submit_err}")
                        results.append({
                            'filename': file_attachments,
                            'error': str(submit_err)
                        })
            
            return {
                'message': f'Submitted {submitted_count} task(s) for processing',
                'code': 200,
                'submitted_count': submitted_count,
                'results': results
            }
            
        except Exception as e:
            logger.error(f"❌ SubmitQueuedTasks error: {e}", exc_info=True)
            return {'msg': str(e), 'code': 500}


class ProcessTaskWithLock(GlobalResource):
    """
    带锁的任务处理接口
    用于实际执行任务处理，使用并发控制机制
    """
    
    def post(self):
        """
        从队列中获取任务并使用锁机制进行处理
        """
        try:
            import json
            from flask import request
            
            data = request.get_json()
            username = data.get('username')
            queue_name = data.get('queue_name')
            message_id = data.get('message_id')
            session_id = data.get('session_id')
            
            if not username:
                raise messages.UserNameNotExistsError
            
            logger.info(f"🔵 ProcessTaskWithLock: user={username}, queue={queue_name}, message_id={message_id}, session_id={session_id}")
            
            # 定义实际的处理函数
            def actual_processor(message_data, attachments):
                """
                实际的业务处理逻辑
                这里应该调用你的 AI API 或其他处理逻辑
                """
                logger.info(f"⚙️ Processing: user={username}, attachments={attachments}, session_id={session_id}")
                
                # TODO: 在这里实现你的业务逻辑
                # 例如：调用 OpenAI API、处理文件等
                
                # 示例：返回成功
                return {
                    'success': True,
                    'processed_files': attachments,
                    'session_id': session_id,
                    'message': 'Task processed successfully'
                }
            
            # 使用带锁的处理方法
            result = TaskQueue.process_with_lock(
                queue_name=queue_name,
                message_id=message_id,
                processor_func=lambda: actual_processor(data, [username]),
                username=username,
                attachment_names=[username],
                message_data=data,
                session_id=session_id
            )
            
            return {
                'success': True,
                'result': result,
                'code': 200
            }
            
        except Exception as e:
            logger.error(f"❌ ProcessTaskWithLock error: {e}", exc_info=True)
            return {'success': False, 'error': str(e), 'code': 500}


system_api.add_resource(TaskQueue, "/task_queue")
system_api.add_resource(QueueState, "/queue_state")
system_api.add_resource(QueueStats, "/queue_stats")
system_api.add_resource(DeleteUploadedRecord, "/delete_uploaded_record")
system_api.add_resource(SubmitQueuedTasks, "/submit_queued_tasks")
system_api.add_resource(ProcessTaskWithLock, "/process_task_with_lock")


# ============================================================
# 使用示例和辅助函数
# ============================================================

def call_with_queue_lock(username: str, queue_name: str, message_id: str, attachment_names: list, message_data: dict, processor_func=None):
    """
    调用带队列锁的任务处理（参考 call_openai_with_global_lock_gpt5）
    
    :param username: 用户名
    :param queue_name: 队列名称 (heavy-queue 或 light-queue)
    :param message_id: 消息 ID
    :param attachment_names: 附件名称列表
    :param message_data: 消息数据
    :param processor_func: 实际的处理函数，如果为 None 则使用默认处理
    :return: 处理结果
    
    使用示例:
    ```python
    @app.route('/api/process_task', methods=['POST'])
    def process_task():
        data = request.get_json()
        username = data.get('username')
        queue_name = data.get('queue_name')
        message_id = data.get('message_id')
        attachment_names = data.get('attachment_names')
        
        try:
            result = call_with_queue_lock(
                username=username,
                queue_name=queue_name,
                message_id=message_id,
                attachment_names=attachment_names,
                message_data=data,
                processor_func=your_actual_processing_function
            )
            return jsonify({"success": True, "result": result})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    ```
    """
    try:
        # 使用带锁的处理方法
        if processor_func is None:
            processor_func = default_task_processor
        
        result = TaskQueue.process_with_lock(
            queue_name=queue_name,
            message_id=message_id,
            processor_func=processor_func,
            username=username,
            attachment_names=attachment_names,
            message_data=message_data,
            session_id=message_data.get('session_id')
        )
        return result
    except Exception as e:
        logger.error(f"❌ 带锁任务处理失败：{str(e)}")
        raise e


def default_task_processor(username: str, attachment_names: list, message_data: dict):
    """
    默认任务处理函数
    在实际使用时，请将此函数替换为您的实际业务逻辑
    """
    logger.info(f"🔵 开始处理任务：user={username}, attachments={attachment_names}")
    
    # 这里应该是您的实际业务逻辑
    # 例如：调用 OpenAI API、处理文件等
    
    # 模拟处理过程
    import time
    time.sleep(5)  # 模拟耗时操作
    
    logger.info(f"✅ 任务处理完成：user={username}")
    return {
        "success": True,
        "message": "Task processed successfully",
        "username": username,
        "processed_files": attachment_names
    }

