import os
import uuid
import logging
import json
from datetime import datetime
from app import messages
from . import system_api
from .args_parser import TaskQueuePostParser, TaskQueueDeleteParser, QueueStateGetParser, QueueStatePostParser, TaskQueueGetParser, TaskQueuePutParser
from flask import current_app
from common.common_resource import GlobalResource
from azure.storage.queue import QueueClient
from azure.core.exceptions import ResourceExistsError
from utils.file_utils import cal_tokens

logger = logging.getLogger(__name__)


class QueueState(GlobalResource):

    @staticmethod
    def create(username: str, queue_name: str, message: str, message_id: str, status: str, account_name: str = None) -> str:
        doc_id = str(uuid.uuid4())
        item = {
            "id": doc_id,
            "type": "queue_state",
            "username": username,
            "queue_name": queue_name,
            "message": message,
            "message_id": message_id,
            "status": status,
            "account_name": account_name,
            "create_time": datetime.now().isoformat()
        }
        current_app.container_task_queue.create_item(body=item)
        return doc_id

    @staticmethod
    def update_status_by_message_id(message_id: str, status: str) -> None:
        query = "SELECT * FROM user u WHERE u.type = 'queue_state' AND u.message_id = @message_id"
        params = [{"name": "@message_id", "value": message_id}]
        items = list(current_app.container_task_queue.query_items(query=query, parameters=params, enable_cross_partition_query=True))
        for item in items:
            item["status"] = status
            item["update_time"] = datetime.now().isoformat()
            current_app.container_task_queue.upsert_item(item)

    def get(self):
        args_parser = QueueStateGetParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        queue_name = args.get("queue_name")
        message_id = args.get("message_id")
        status = args.get("status")
        query = "SELECT * FROM user u WHERE u.type = 'queue_state'"
        params = []
        if username:
            query += " AND u.username = @username"
            params.append({"name": "@username", "value": username})
        if queue_name:
            query += " AND u.queue_name = @queue_name"
            params.append({"name": "@queue_name", "value": queue_name})
        if message_id:
            query += " AND u.message_id = @message_id"
            params.append({"name": "@message_id", "value": message_id})
        if status:
            query += " AND u.status = @status"
            params.append({"name": "@status", "value": status})
        items = list(current_app.container_task_queue.query_items(query=query, parameters=params, enable_cross_partition_query=True))
        result = []
        for item in items:
            result.append({
                "id": item.get("id", ""),
                "username": item.get("username", ""),
                "queue_name": item.get("queue_name", ""),
                "message": item.get("message", ""),
                "message_id": item.get("message_id", ""),
                "status": item.get("status", ""),
                "create_time": item.get("create_time", ""),
                "update_time": item.get("update_time", "")
            })
        return {"count": len(result), "queue_state": result, "code": 200, "params": params}

    def post(self):
        args_parser = QueueStatePostParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        queue_name = args.get("queue_name")
        message = args.get("message")
        message_id = args.get("message_id")
        status = args.get("status")
        doc_id = QueueState.create(
            username=username,
            queue_name=queue_name,
            message=message,
            message_id=message_id,
            status=status
        )
        return {"id": doc_id, "code": 200}


class TaskQueue(GlobalResource):
    HEAVY_QUEUE_THRESHOLD = 5000

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
            "message": message_content
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
                status=status,
                account_name=account_name
            )
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
            QueueState.update_status_by_message_id(message_id, "completed")
            logger.info(f"user: {username} delete message from queue: {queue_name}")
            return {"msg": "success", "code": 200}
        except Exception as e:
            logger.error(f"Failed to delete message from queue {queue_name}: {e}")
            return {"msg": str(e), "code": 500}


system_api.add_resource(TaskQueue, "/task_queue")
system_api.add_resource(QueueState, "/queue_state")

