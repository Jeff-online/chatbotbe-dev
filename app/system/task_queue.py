import os
import uuid
import logging
from datetime import datetime
from app import messages
from . import system_api
from .args_parser import TaskQueuePostParser, TaskQueueDeleteParser, QueueStateGetParser, QueueStatePostParser
from flask import current_app
from common.common_resource import GlobalResource
from azure.storage.queue import QueueClient
from azure.core.exceptions import ResourceExistsError

logger = logging.getLogger(__name__)


class QueueState(GlobalResource):

    @staticmethod
    def create(username: str, queue_name: str, message: str, message_id: str, status: str) -> str:
        doc_id = str(uuid.uuid4())
        item = {
            "id": doc_id,
            "type": "queue_state",
            "username": username,
            "queue_name": queue_name,
            "message": message,
            "message_id": message_id,
            "status": status,
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
        return {"queue_state": result, "code": 200, "params": params}

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
        message = args.get("message")

        if not username:
            raise messages.UserNameNotExistsError

        try:
            queue_client = self._get_queue_client(queue_name)
            try:
                queue_client.create_queue()
            except ResourceExistsError:
                pass
            send_result = queue_client.send_message(message)
            queue_state_id = QueueState.create(
                username=username,
                queue_name=queue_name,
                message=message,
                message_id=send_result.id,
                status="queued"
            )
            logger.info(f"user: {username} send message to queue: {queue_name}")
            return {
                "message_id": send_result.id,
                "pop_receipt": send_result.pop_receipt,
                "insertion_time": str(send_result.inserted_on) if getattr(send_result, "inserted_on", None) else None,
                "queue_state_id": queue_state_id,
                "code": 200
            }
        except Exception as e:
            logger.error(f"Failed to send message to queue {queue_name}: {e}")
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
            QueueState.update_status_by_message_id(message_id, "deleted")
            logger.info(f"user: {username} delete message from queue: {queue_name}")
            return {"msg": "success", "code": 200}
        except Exception as e:
            logger.error(f"Failed to delete message from queue {queue_name}: {e}")
            return {"msg": str(e), "code": 500}


system_api.add_resource(TaskQueue, "/task_queue")
system_api.add_resource(QueueState, "/queue_state")

