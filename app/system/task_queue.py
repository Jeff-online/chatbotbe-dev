import os
import uuid
import logging
import json
import time
import random
from datetime import datetime, timedelta, timezone
from app import messages
from . import system_api
from .args_parser import TaskQueuePostParser, QueueStateGetParser, QueueStatePostParser
from flask import current_app
from common.common_resource import GlobalResource
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
    # 动态并发：如果有 heavy 任务，light 最多 2 个；如果没有 heavy 任务，light 最多 3 个
    MAX_LIGHT_WITH_HEAVY = 2
    MAX_LIGHT_WITHOUT_HEAVY = 3
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
    def _get_active_slots(lock_container, lock_id, queue_name):
        """获取并清理超时的活跃槽位"""
        try:
            lock_doc = lock_container.read_item(item=lock_id, partition_key=lock_id)
            current_time = datetime.now(timezone.utc)
            processing_slots = lock_doc.get('processing_slots', [])
            
            cleaned_slots = []
            for slot in processing_slots:
                locked_at_str = slot.get('locked_at')
                if locked_at_str:
                    try:
                        locked_at_time = datetime.fromisoformat(locked_at_str.replace('Z', '+00:00'))
                        timeout_threshold = current_time - timedelta(minutes=QueueConcurrencyLock.PROCESSING_TIMEOUT_MINUTES)
                        if locked_at_time < timeout_threshold:
                            logger.warning(f"⚠️ 检测到{queue_name}锁槽位超时，强制释放：{slot.get('record_id')}")
                        else:
                            cleaned_slots.append(slot)
                    except:
                        cleaned_slots.append(slot)
                else:
                    cleaned_slots.append(slot)
            
            if len(cleaned_slots) != len(processing_slots):
                lock_doc['processing_slots'] = cleaned_slots
                lock_container.replace_item(item=lock_id, body=lock_doc)
            
            return cleaned_slots, lock_doc
        except CosmosHttpResponseError as e:
            if e.status_code == 404:
                return [], None
            raise

    @staticmethod
    def acquire_lock(queue_name: str, record_id: str, session_id: str = None) -> bool:
        """
        获取队列处理锁
        """
        lock_container = QueueConcurrencyLock.get_lock_container()
        
        while True:
            try:
                # 1. 检查是否有 Heavy 任务在运行
                heavy_slots, heavy_doc = QueueConcurrencyLock._get_active_slots(
                    lock_container, QueueConcurrencyLock.HEAVY_LOCK_ID, "heavy-queue"
                )
                is_heavy_running = len(heavy_slots) > 0

                if queue_name == "heavy-queue":
                    if not is_heavy_running:
                        # 尝试获取 Heavy 锁
                        if heavy_doc is None:
                            heavy_doc = {
                                "id": QueueConcurrencyLock.HEAVY_LOCK_ID,
                                "processing_slots": [],
                                "created_at": datetime.now(timezone.utc).isoformat()
                            }
                        
                        heavy_doc['processing_slots'].append({
                            'record_id': record_id,
                            'session_id': session_id,
                            'locked_at': datetime.now(timezone.utc).isoformat()
                        })
                        
                        try:
                            if heavy_doc.get('_etag'):
                                lock_container.replace_item(item=heavy_doc['id'], body=heavy_doc)
                            else:
                                lock_container.create_item(body=heavy_doc)
                            logger.info(f"✅ 成功获取 heavy-queue 锁")
                            return True
                        except CosmosHttpResponseError as e:
                            if e.status_code == 412: continue
                            raise
                    else:
                        logger.info(f"⏳ heavy-queue 已有任务在运行，任务 {record_id} 等待中...")
                
                elif queue_name == "light-queue":
                    # 2. 确定 Light 任务的并发上限
                    max_light = QueueConcurrencyLock.MAX_LIGHT_WITH_HEAVY if is_heavy_running else QueueConcurrencyLock.MAX_LIGHT_WITHOUT_HEAVY
                    
                    light_slots, light_doc = QueueConcurrencyLock._get_active_slots(
                        lock_container, QueueConcurrencyLock.LIGHT_LOCK_ID, "light-queue"
                    )
                    
                    if len(light_slots) < max_light:
                        if light_doc is None:
                            light_doc = {
                                "id": QueueConcurrencyLock.LIGHT_LOCK_ID,
                                "processing_slots": [],
                                "created_at": datetime.now(timezone.utc).isoformat()
                            }
                        
                        light_doc['processing_slots'].append({
                            'record_id': record_id,
                            'session_id': session_id,
                            'locked_at': datetime.now(timezone.utc).isoformat()
                        })
                        
                        try:
                            if light_doc.get('_etag'):
                                lock_container.replace_item(item=light_doc['id'], body=light_doc)
                            else:
                                lock_container.create_item(body=light_doc)
                            logger.info(f"✅ 成功获取 light-queue 锁 (当前活跃: {len(light_doc['processing_slots'])}/{max_light}, Heavy状态: {'运行中' if is_heavy_running else '未运行'})")
                            return True
                        except CosmosHttpResponseError as e:
                            if e.status_code == 412: continue
                            raise
                    else:
                        logger.info(f"⏳ light-queue 已达上限 ({len(light_slots)}/{max_light})，任务 {record_id} 等待中...")

                time.sleep(QueueConcurrencyLock.RETRY_INTERVAL_SECONDS)
                
            except Exception as e:
                logger.error(f"❌ 获取锁时发生错误：{e}")
                time.sleep(QueueConcurrencyLock.RETRY_INTERVAL_SECONDS)
    
    @staticmethod
    def release_lock(queue_name: str, record_id: str) -> bool:
        """
        释放队列处理锁
        :param queue_name: 队列名称
        :param record_id: 数据库记录 ID
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
                if slot.get('record_id') != record_id
            ]
            
            if len(lock_doc['processing_slots']) < original_count:
                lock_container.replace_item(item=lock_doc, body=lock_doc)
                logger.info(f"✅ 成功释放{queue_name}锁槽位，当前活跃数：{len(lock_doc['processing_slots'])}")
                return True
            else:
                logger.warning(f"⚠️ 未找到要释放的锁槽位，record_id: {record_id}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 释放锁失败：{e}")
            return False


class QueueState(GlobalResource):

    @staticmethod
    def create(username: str, queue_name: str, message: str, status: str, session_id: str = None) -> str:
        """
        创建队列状态记录
        """
        logger.info(f"🔵 QueueState.create started for user: {username}, queue: {queue_name}, status: {status}")
        
        try:
            doc_id = str(uuid.uuid4())
            item = {
                "id": doc_id,
                "type": "queue_state",
                "username": username,
                "queue_name": queue_name,
                "message": message,
                "status": status,
                "create_time": datetime.now().isoformat()
            }
            if session_id:
                item["session_id"] = session_id
            
            logger.info(f"📝 Preparing to insert item into Cosmos DB: {doc_id}")
            
            if not hasattr(current_app, 'container_task_queue') or current_app.container_task_queue is None:
                logger.error("❌ current_app.container_task_queue is NOT initialized!")
                raise Exception("Cosmos DB container not initialized in current_app")
            
            # 直接尝试创建 item
            result = current_app.container_task_queue.create_item(body=item)
            logger.info(f"✅ Queue state item created successfully in database, id: {result.get('id')}")
            return doc_id
            
        except Exception as e:
            logger.error(f"❌ Error in QueueState.create: {str(e)}", exc_info=True)
            raise

    @staticmethod
    def update_status_by_id(doc_id: str, status: str) -> None:
        """
        根据记录 ID 直接更新队列状态
        """
        try:
            item = current_app.container_task_queue.read_item(item=doc_id, partition_key=doc_id)
            item["status"] = status
            item["update_time"] = datetime.now().isoformat()
            current_app.container_task_queue.upsert_item(item)
            logger.info(f"✅ Updated queue state to '{status}' for record id: {doc_id}")
        except Exception as e:
            logger.error(f"❌ Failed to update status by id {doc_id}: {e}")

    @staticmethod
    def get_record_by_filename(username: str, filename: str, session_id: str = None):
        """
        根据文件名和会话 ID 获取队列状态记录
        """
        query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username"
        params = [{"name": "@username", "value": username}]
        if session_id:
            query += " AND c.session_id = @session_id"
            params.append({"name": "@session_id", "value": session_id})
        
        try:
            items = list(current_app.container_task_queue.query_items(query=query, parameters=params, enable_cross_partition_query=True))
            for item in items:
                message_data = item.get("message", {})
                if isinstance(message_data, str):
                    try: message_data = json.loads(message_data)
                    except: continue
                
                attachments = message_data.get("attachment_names", []) if isinstance(message_data, dict) else []
                if filename in attachments:
                    return item
            return None
        except Exception as e:
            logger.error(f"❌ Error getting record by filename: {e}")
            return None

    @staticmethod
    def update_status_by_filename(username: str, filename: str, status: str) -> None:
        """
        根据用户名和文件名更新队列状态
        :param username: 用户名
        :param filename: 文件名
        :param status: 新状态
        """
        query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username"
        params = [{"name": "@username", "value": username}]
        
        try:
            items = list(current_app.container_task_queue.query_items(query=query, parameters=params, enable_cross_partition_query=True))
            for item in items:
                message_data = item.get("message", {})
                if isinstance(message_data, str):
                    try:
                        message_data = json.loads(message_data)
                    except:
                        continue
                
                attachments = message_data.get("attachment_names", [])
                if filename in attachments:
                    item["status"] = status
                    item["update_time"] = datetime.now().isoformat()
                    current_app.container_task_queue.upsert_item(item)
                    logger.info(f"✅ Updated queue state to '{status}' for file: {filename}")
        except Exception as e:
            logger.error(f"❌ Failed to update status by filename: {e}")

    @staticmethod
    def delete_by_filename(username: str, filename: str, session_id: str = None) -> None:
        """根据用户名、文件名（可选：session_id）删除队列记录"""
        logger.info(f"🔵 Attempting to delete queue record for user: {username}, file: {filename}, session_id: {session_id}")
        
        # 查询记录
        query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username"
        params = [{"name": "@username", "value": username}]
        
        if session_id:
            query += " AND c.session_id = @session_id"
            params.append({"name": "@session_id", "value": session_id})
        
        try:
            items = list(current_app.container_task_queue.query_items(query=query, parameters=params, enable_cross_partition_query=True))
            logger.info(f"🔍 Found {len(items)} records for user {username}")
            
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
                    # 从 Cosmos DB 中删除记录（所有状态）
                    doc_id = item.get("id")
                    if doc_id:
                        current_app.container_task_queue.delete_item(item=doc_id, partition_key=doc_id)
                        logger.info(f"✅ Deleted queue state record {doc_id} for file {filename}")
                        deleted_count += 1
            
            if deleted_count > 0:
                logger.info(f"✅ Successfully deleted {deleted_count} record(s) for file {filename}")
            else:
                logger.warning(f"⚠️ No records found for file {filename}")
                
        except Exception as e:
            logger.error(f"❌ Error during delete_by_filename: {e}", exc_info=True)
            raise e

    @staticmethod
    def delete_all_by_username(username: str) -> int:
        """
        删除指定用户的所有队列记录
        :param username: 用户名
        :return: 删除的记录数量
        """
        logger.info(f"🔵 Deleting all queue records for user: {username}")
        query = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username"
        params = [{"name": "@username", "value": username}]
        
        try:
            items = list(current_app.container_task_queue.query_items(query=query, parameters=params, enable_cross_partition_query=True))
            deleted_count = 0
            for item in items:
                doc_id = item.get("id")
                if doc_id:
                    current_app.container_task_queue.delete_item(item=doc_id, partition_key=doc_id)
                    deleted_count += 1
            logger.info(f"✅ Successfully deleted {deleted_count} queue records for user {username}")
            return deleted_count
        except Exception as e:
            logger.error(f"❌ Failed to delete all records for user {username}: {e}")
            return 0

    def get(self):
        args_parser = QueueStateGetParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        queue_name = args.get("queue_name")
        status = args.get("status")
        query = "SELECT * FROM c WHERE c.type = 'queue_state'"
        params = []
        if username:
            query += " AND c.username = @username"
            params.append({"name": "@username", "value": username})
        if queue_name:
            query += " AND c.queue_name = @queue_name"
            params.append({"name": "@queue_name", "value": queue_name})
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
        status = args.get("status")
        session_id = args.get("session_id")
        doc_id = QueueState.create(
            username=username,
            queue_name=queue_name,
            message=message,
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
        
        # 1. 查询当前用户的待处理信息 (用于前端统计本地状态)
        query_user_pending = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username AND c.status NOT IN ('parsed', 'failed')"
        params_user = [{"name": "@username", "value": username}] if username else []
        
        items_user_pending = []
        if username:
            try:
                items_user_pending = list(current_app.container_task_queue.query_items(
                    query=query_user_pending, 
                    parameters=params_user,
                    enable_cross_partition_query=True
                ))
            except Exception as e:
                logger.error(f"❌ Error querying user pending items: {e}")
        
        # 2. 查询指定用户的已解析完成任务
        query_parsed = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.username = @username AND c.status = 'parsed'"
        params_parsed = [{"name": "@username", "value": username}] if username else []
        
        items_parsed = []
        if username:
            try:
                items_parsed = list(current_app.container_task_queue.query_items(
                    query=query_parsed,
                    parameters=params_parsed,
                    enable_cross_partition_query=True
                ))
            except Exception as e:
                logger.error(f"❌ Error querying parsed items: {e}")
        
        # 3. 计算全局排队位置 (前面排队的附件总数)
        # 为了更健壮，我们查询所有非完成状态的任务，并在 Python 中手动排序，避免 Cosmos DB 索引限制
        query_all_active = "SELECT * FROM c WHERE c.type = 'queue_state' AND c.status NOT IN ('parsed', 'failed')"
        
        all_active_items = []
        try:
            all_active_items = list(current_app.container_task_queue.query_items(
                query=query_all_active, 
                enable_cross_partition_query=True
            ))
            logger.info(f"🔍 Found {len(all_active_items)} total active queue items in database")
        except Exception as e:
            logger.error(f"❌ Error querying all active items: {e}")
        
        # 统计当前用户的待处理详情
        total_pending = 0
        light_queue_pending = 0
        heavy_queue_pending = 0
        light_attachment_names = []
        heavy_attachment_names = []
        
        for item in items_user_pending:
            q_name = item.get("queue_name", "")
            message_data = item.get("message", {})
            if isinstance(message_data, str):
                try: message_data = json.loads(message_data)
                except: pass
            attachment_names = message_data.get("attachment_names", []) if isinstance(message_data, dict) else []
            
            total_pending += 1
            if q_name == "light-queue":
                light_queue_pending += 1
                if attachment_names: light_attachment_names.extend(attachment_names)
            elif q_name == "heavy-queue":
                heavy_queue_pending += 1
                if attachment_names: heavy_attachment_names.extend(attachment_names)
        
        # 统计已解析
        total_parsed = len(items_parsed)
        parsed_attachment_names = []
        for item in items_parsed:
            message_data = item.get("message", {})
            if isinstance(message_data, str):
                try: message_data = json.loads(message_data)
                except: pass
            attachment_names = message_data.get("attachment_names", []) if isinstance(message_data, dict) else []
            if attachment_names: parsed_attachment_names.extend(attachment_names)
        
        # 精确计算排队位置 (排在当前用户第一个活跃任务前的所有附件总数)
        # 在 Python 中按照时间戳和 SessionID 排序，确保逻辑一致
        sorted_all = sorted(all_active_items, key=lambda x: (x.get('_ts', 0), x.get('session_id', '')))
        
        queue_position = 0
        if username:
            user_task_index = -1
            # 找到当前用户最早的一个任务
            for i, task in enumerate(sorted_all):
                if task.get("username") == username:
                    user_task_index = i
                    break
            
            if user_task_index > 0:
                # 累加排在前面的所有任务的附件数量
                for i in range(user_task_index):
                    ahead_task = sorted_all[i]
                    msg_data = ahead_task.get("message", {})
                    if isinstance(msg_data, str):
                        try: msg_data = json.loads(msg_data)
                        except: pass
                    
                    if isinstance(msg_data, dict):
                        ahead_attachments = msg_data.get("attachment_names", [])
                        queue_position += len(ahead_attachments)
                logger.info(f"📊 Calculated queue_position for {username}: {queue_position} (ahead of index {user_task_index})")
            elif user_task_index == -1:
                # 如果当前用户的任务还没进入 active 状态 (理论上不应该，因为我们包含所有非完成状态)
                # 那么全局所有正在排队/处理的任务都算在前面
                for task in sorted_all:
                    msg_data = task.get("message", {})
                    if isinstance(msg_data, str):
                        try: msg_data = json.loads(msg_data)
                        except: pass
                    if isinstance(msg_data, dict):
                        queue_position += len(msg_data.get("attachment_names", []))
                logger.info(f"📊 User {username} not found in active queue, total active attachments: {queue_position}")
            else:
                logger.info(f"📊 User {username} is at the front of the queue")
        
        return {
            "total_pending": total_pending,
            "light_queue_pending": light_queue_pending,
            "heavy_queue_pending": heavy_queue_pending,
            "light_attachment_names": light_attachment_names,
            "heavy_attachment_names": heavy_attachment_names,
            "total_parsed": total_parsed,
            "parsed_attachment_names": parsed_attachment_names,
            "queue_position": queue_position,
            "code": 200
        }


class TaskQueue(GlobalResource):
    HEAVY_QUEUE_THRESHOLD = 30000

    @staticmethod
    def _truncate_message(message: str, limit: int = 1024) -> str:
        """
        Truncate message content if it exceeds the limit.
        """
        if message and len(message) > limit:
            logger.warning(f"⚠️ Message truncated from {len(message)} to {limit} characters")
            return message[:limit] + "..."
        return message

    def post(self):
        args_parser = TaskQueuePostParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        queue_name = args.get("queue_name")
        message_content = args.get("message")
        attachment_names = args.get("attachment_names")
        session_id = args.get("session_id")

        if not username:
            raise messages.UserNameNotExistsError

        # Truncate message content if it's too large (limit to 1K)
        truncated_content = self._truncate_message(message_content)

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
            "queue_name": queue_name,
            "user-name": username,
            "create_time": create_time,
            "status": status,
            "message": truncated_content,
            "attachment_names": attachment_names if attachment_names else [],
            "session_id": session_id
        }
        message_json = json.dumps(message_payload)

        try:
            queue_state_id = QueueState.create(
                username=username,
                queue_name=queue_name,
                message=message_json,
                status=status,
                session_id=session_id
            )
            logger.info(f"✅ Created queue record {queue_state_id} for user {username}")
            return {
                "queue_state_id": queue_state_id,
                "queue_name": queue_name,
                "code": 200
            }
        except Exception as e:
            logger.error(f"Failed to create queue record for user {username}: {e}")
            return {"msg": str(e), "code": 500}

    @staticmethod
    def process_with_lock(queue_name: str, record_id: str, processor_func, *args, **kwargs):
        """
        带锁的任务处理方法，确保同一时间只有指定数量的任务在处理
        :param queue_name: 队列名称 (heavy-queue 或 light-queue)
        :param record_id: 数据库记录 ID
        :param processor_func: 实际的处理函数
        :param args: 处理函数的参数
        :param kwargs: 处理函数的关键字参数
        :return: 处理结果
        """
        lock_acquired = False
        
        try:
            # 1. 获取锁（会阻塞直到获取到锁）
            session_id = kwargs.get('session_id')
            logger.info(f"🔵 开始为任务 {record_id} 获取{queue_name}锁 (session_id={session_id})...")
            lock_acquired = QueueConcurrencyLock.acquire_lock(queue_name, record_id, session_id=session_id)
            
            if not lock_acquired:
                logger.error(f"❌ 任务 {record_id} 获取锁失败")
                raise Exception("Failed to acquire concurrency lock")
            
            logger.info(f"✅ 任务 {record_id} 成功获取锁，开始处理...")
            
            # 2. 更新状态为 processing
            QueueState.update_status_by_id(record_id, "processing")
            
            # 3. 执行实际的处理逻辑
            result = processor_func(*args, **kwargs)
            
            # 4. 更新状态为 parsed (表示文件已解析完成，AI 已返回响应)
            QueueState.update_status_by_id(record_id, "parsed")
            
            logger.info(f"✅ 任务 {record_id} 处理完成，状态已更新为 'parsed'")
            return result
            
        except Exception as e:
            logger.error(f"❌ 任务 {record_id} 处理失败：{e}", exc_info=True)
            QueueState.update_status_by_id(record_id, "failed")
            raise e
            
        finally:
            # 5. 释放锁（无论成功或失败）
            if lock_acquired:
                try:
                    QueueConcurrencyLock.release_lock(queue_name, record_id)
                    logger.info(f"✅ 任务 {record_id} 已释放锁")
                except Exception as release_error:
                    logger.error(f"❌ 释放锁失败：{release_error}")


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
                        
                        # 如果有传入新的 session_id，则更新
                        if session_id:
                            item['session_id'] = session_id
                        
                        # 更新 message 内容
                        message_data['status'] = 'queued'
                        message_data['message'] = message_data.get('message', '').replace('(waiting for submit)', '')
                        # 清理 message 内部的 session_id（如果有）
                        if 'session_id' in message_data:
                            del message_data['session_id']
                            
                        item['message'] = json.dumps(message_data)
                        
                        current_app.container_task_queue.upsert_item(item)
                        
                        logger.info(f"✅ Submitted task for file: {file_attachments}, queue: {item.get('queue_name')}")
                        
                        submitted_count += 1
                        results.append({
                            'filename': file_attachments,
                            'queue_name': item.get('queue_name', 'light-queue'),
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
            record_id = data.get('record_id')
            session_id = data.get('session_id')
            
            if not username:
                raise messages.UserNameNotExistsError
            
            logger.info(f"🔵 ProcessTaskWithLock: user={username}, queue={queue_name}, record_id={record_id}, session_id={session_id}")
            
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
                record_id=record_id,
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

def call_with_queue_lock(username: str, queue_name: str, record_id: str, attachment_names: list, message_data: dict, processor_func=None):
    """
    调用带队列锁的任务处理（参考 call_openai_with_global_lock_gpt5）
    
    :param username: 用户名
    :param queue_name: 队列名称 (heavy-queue 或 light-queue)
    :param record_id: 数据库记录 ID
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
        record_id = data.get('record_id')
        attachment_names = data.get('attachment_names')
        
        try:
            result = call_with_queue_lock(
                username=username,
                queue_name=queue_name,
                record_id=record_id,
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
            record_id=record_id,
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

