import os
import time
import uuid
import shutil
import chardet
import logging
from app import messages
from . import system_api
from . args_parser import *
from flask import current_app
from datetime import datetime, timedelta
from utils.file_utils import FileOperation, cal_tokens
from .args_parser import CheckTokenParser
from werkzeug.utils import secure_filename
from common.common_resource import GlobalResource, Resource
import json
from .task_queue import TaskQueue, QueueState
logger = logging.getLogger(__name__)


class SessionManagement(GlobalResource):

    def get(self):
        """セッションリスト"""
        args_parser = SessionParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        session_id = args.get("session_id")
        if not username:
            raise messages.UserNameNotExistsError
        user_data = list(current_app.container.query_items(
            query=f"SELECT * FROM user u WHERE u.username = '{username}'",
            enable_cross_partition_query=True
        ))
        if user_data:
            user_info = user_data[0]
            user_info_allow = self.check_session(user_info)
            u_sessions = user_info_allow.get("U_session", [])
            if not session_id:
                title_result = [{"session_id": session["session_id"], "title": session["title"]} for session in
                                u_sessions]
                return {"session_data": title_result, "code": 200}
            history_data = list(current_app.container_c.query_items(
                query=f"SELECT * FROM user s WHERE s.session_id = '{session_id}'",
                enable_cross_partition_query=True
            ))
            if history_data:
                history_info = history_data[0]
                return {"content_data": history_info["S_info"], "code": 200}
            return {"msg": "データ存在しない", "code": 404}
        else:
            raise messages.UserNotExistsError

    def post(self):
        """セッション内容の追加"""
        args_parser = SessionAddParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        if not username:
            raise messages.UserNameNotExistsError
        user_data = list(current_app.container.query_items(
            query=f"SELECT * FROM user u WHERE u.username = '{username}'",
            enable_cross_partition_query=True
        ))
        if user_data:
            session_id = str(uuid.uuid4())
            user_info = user_data[0]
            session_data = {
                "session_id": session_id,
                "title": "新しい会話",
                "create_time": datetime.now().isoformat()
            }
            user_sessions = user_info.get("U_session", [])
            user_sessions.append(session_data)
            user_info["U_session"] = user_sessions
            current_app.container.upsert_item(user_info)

            history_data = {
                "id": session_id,
                "session_id": session_id,
                "S_info": {"content": []}
            }
            current_app.container_c.upsert_item(history_data)

            session_info = {
                "session_id": session_data["session_id"]
            }
            logger.info("New Session Successful")
            return {'session_info': session_info, 'code': 200}
        else:
            raise messages.UserNotExistsError

    def put(self):
        """セッションの変更"""
        args_parser = SessionPutParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        prompt_name = args.get("prompt_name")
        attachment_names = args.get("attachment_name")   # 这里可能是 str 或 list
        content = args.get("content")
        session_id = args.get("session_id")
        deploy_model = args.get("deploy_model")
        if not username:
            raise messages.UserNameNotExistsError
        user_data = list(current_app.container.query_items(
            query=f"SELECT * FROM user u WHERE u.username = '{username}'",
            enable_cross_partition_query=True
        ))

        if user_data:
            user_info = user_data[0]
            try:
                result = self.check_name(prompt_name, username, attachment_names)
                if isinstance(result, dict) and "message" in result:
                    # check_name returned an error dict
                    return result
                elif isinstance(result, tuple) and len(result) == 2:
                    # Normal case: (clue, file_content)
                    clue, file_content = result
                else:
                    # Unexpected return format
                    return {"message": f"Unexpected return format from check_name: {type(result)}", "status": 404, "attachment_names": attachment_names}
            except Exception as e:
                return {"message check name after check_name": str(e), "status": 404,"attachment_names": attachment_names}

            dialogue_history = [
                {"role": "system", "content": "You are a friendly and knowledgeable AI assistant that can answer a variety of user questions and provide assistance."}
            ]

            for session in user_info.get("U_session", []):
                if session["session_id"] == session_id:
                    if session["title"] == "新しい会話":
                        session["title"] = content
                        current_app.container.upsert_item(user_info)

                    session_data = list(current_app.container_c.query_items(
                        query=f"SELECT * FROM user s WHERE s.session_id = '{session_id}'",
                        enable_cross_partition_query=True
                    ))
                    if session_data:
                        session_info = session_data[0]
                        history_data = session_info["S_info"]["content"]

                        try:
                            content = clue + content
                            content, response_ai, used_model = self.get_answer(file_content, content, dialogue_history, history_data, deploy_model)
                        except Exception as e:
                            return {"message": str(e), "status": 404}

                        history_data.append([content, response_ai])
                        session_info["S_info"] = {"content": history_data}
                        current_app.container_c.upsert_item(session_info)

                        session_info = {
                            "response_ai": response_ai,
                            "session_id": session_id
                        }
                        logger.info(f"{username} input: {content}")
                        return {'session_info': session_info, 'code': 200, 'dialogue_history': dialogue_history, 'deploy_model': used_model}
            return {"msg": "データ存在しない", "code": 404}
        else:
            raise messages.UserNotExistsError

    def delete(self):
        """セッションの削除"""
        args_parser = SessionParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        session_id = args.get("session_id")
        if not username:
            raise messages.UserNameNotExistsError
        user_data = list(current_app.container.query_items(
            query=f"SELECT * FROM u WHERE u.username = '{username}'",
            enable_cross_partition_query=True
        ))
        if user_data:
            user_info = user_data[0]
            if session_id:
                sessions = user_info.get("U_session", [])
                updated_sessions = [session for session in sessions if session["session_id"] != session_id]
                user_info["U_session"] = updated_sessions
                current_app.container.upsert_item(user_info)

                history_data = list(current_app.container_c.query_items(
                    query=f"SELECT * FROM s WHERE s.session_id = '{session_id}'",
                    enable_cross_partition_query=True
                ))
                if history_data:
                    current_app.container_c.delete_item(session_id, partition_key=session_id)

                    logger.info(f"user: {username}\n option: Session deleted successfully")
                    return {'msg': 'success', 'code': 200, 'session_id': session_id}
            raise messages.SessionIdNotExistsError
        raise messages.UserNotExistsError

    @staticmethod
    def get_answer(file_content: dict, input_data: str, question: list, history=None, deploy_model=None):
        """
        file_content: dict {filename: {"text": str, "images": [base64,...]}, ...}
        """

        if history:
            for data in history:
                history_message = {"role": "user", "content": data[0]}
                question.append(history_message)

        # 拼接多个文件内容
        merged_texts = []
        merged_images = []
        if file_content:
            for fname, fdata in file_content.items():
                if fdata["text"]:
                    merged_texts.append(f"[{fname}]\n{fdata['text']}")
                if fdata["images"]:
                    merged_images.extend(fdata["images"])

        # 整合文本
        if merged_texts:
            input_data = "\n\n".join(merged_texts) + "\n\n" + input_data

        # 构造 message
        if merged_images:
            message = {"role": "user", "content": [{"type": "text", "text": input_data}]}
            for base64_img in merged_images:
                message["content"].append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{base64_img}"}
                })
        else:
            message = {"role": "user", "content": input_data}

        question.append(message)

        # token refresh
        if time.time() >= current_app.token_expires - 600:
            new_token = current_app.credential.get_token(os.getenv("SCOPE"))
            current_app.openai_token = new_token.token
            current_app.token_expires = new_token.expires_on
        current_app.openai.api_key = current_app.openai_token
        model_key = (deploy_model or current_app.default_model or "gpt-5").lower()
        config = current_app.model_configs.get(model_key) or current_app.model_configs.get("gpt-5")
        current_app.openai.api_base = config["endpoint"]
        current_app.openai.api_version = config["api_version"]
        if model_key == "gpt-4o":
            response = current_app.openai.ChatCompletion.create(
                deployment_id=config["deployment"],
                messages=question,
                max_tokens=4000,
                temperature=0,
                seed=42
            )
        else:
            response = current_app.openai.ChatCompletion.create(
                deployment_id=config["deployment"],
                messages=question,
                max_completion_tokens=4000,
                temperature=0,
                seed=42
            )
        answer = response['choices'][0]['message']['content'].strip()
        return input_data, answer, model_key

    @staticmethod
    def check_session(session_data):
        old_time = datetime.now() - timedelta(days=30)
        sessions = session_data.get("U_session", [])
        updated_sessions = []
        for session in sessions:
            create_time = session["create_time"]
            if datetime.fromisoformat(create_time) > old_time:
                updated_sessions.append(session)
        session_data["U_session"] = updated_sessions
        current_app.container.upsert_item(session_data)
        return session_data

    def read_txt(self, username, prompt_name):
        blob_client = current_app.container_client.get_blob_client(f"{username}/{prompt_name}")
        stream = blob_client.download_blob().readall()
        encoding = chardet.detect(stream)["encoding"]
        txt_content = stream.decode(encoding)
        return txt_content

    def check_name(self, prompt_name, username, attachment_name):
        if prompt_name:
            clue = self.read_txt(username, prompt_name)
        else:
            clue = ""
        
        file_content = {}  # 初始化默认值
        
        try:
            if attachment_name:
                # 确保 attachment_name 是 list 格式
                if isinstance(attachment_name, str):
                    attachment_name = [attachment_name]  # 转换为列表
                elif not isinstance(attachment_name, list):
                    attachment_name = list(attachment_name) if attachment_name else []
                
                if attachment_name:  # 如果列表不为空
                    get_file_content = FileOperation()
                    file_content = get_file_content(username, attachment_name)  # 返回 dict
                    print(f"🔍 DEBUG: check_name处理结果 - 文件列表: {attachment_name}")
                    print(f"🔍 DEBUG: file_content keys: {list(file_content.keys()) if file_content else 'None'}")
        except Exception as e:
            logger.error(f"Error occurred while getting file content: {e}")
            return {"message in the check name function after FileOperation": str(e), "status": 404, "attachment_name_type": type(attachment_name)}
        
        return clue, file_content

    @staticmethod
    def detect_encoding(file_path):
        with open(file_path, "rb") as file:
            raw_data = file.read(10000)
            result = chardet.detect(raw_data)
            file.seek(0)
        return result["encoding"]

class FileManagement(GlobalResource):

    def post(self):
        """ファイルのアップロード"""
        args_parser = FileParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        file = args.get("file")
        session_id = args.get("session_id")
        if not username:
            raise messages.UserNameNotExistsError
        if file and self.allowed_file(file.filename):
            try:
                # 1. Upload to Blob Storage
                blob_client = current_app.container_client.get_blob_client(f"{username}/{file.filename}")
                blob_client.upload_blob(file.stream, overwrite=True)
                logger.info(f"File '{file.filename}' uploaded successfully to Azure Blob Storage")

                # 2. Determine Queue (Light vs Heavy)
                attachment_names = [file.filename]
                token_result = cal_tokens(username, attachment_names)
                total_tokens = token_result.get("total_tokens", 0)
                
                queue_name = "heavy-queue" if total_tokens > TaskQueue.HEAVY_QUEUE_THRESHOLD else "light-queue"
                
                # 3. Send message to Azure Queue
                create_time = datetime.now().isoformat()
                status = "queued"
                message_payload = {
                    "account_name": None,
                    "queue_name": queue_name,
                    "user-name": username,
                    "create_time": create_time,
                    "status": status,
                    "message": f"File uploaded: {file.filename}",
                    "attachment_names": attachment_names
                }
                message_json = json.dumps(message_payload)
                
                try:
                    # 获取 QueueClient
                    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
                    from azure.storage.queue import QueueClient
                    from azure.core.exceptions import ResourceExistsError
                    queue_client = QueueClient.from_connection_string(connection_string, queue_name)
                    try:
                        queue_client.create_queue()
                    except ResourceExistsError:
                        pass
                    
                    send_result = queue_client.send_message(message_json)
                    logger.info(f"✅ Sent message to queue {queue_name} for file {file.filename}, message_id: {send_result.id}")
                    
                    # 4. Create QueueState record in Cosmos DB
                    queue_state_id = QueueState.create(
                        username=username,
                        queue_name=queue_name,
                        message=message_json,
                        message_id=send_result.id,
                        pop_receipt=send_result.pop_receipt,
                        status=status,
                        session_id=session_id
                    )
                    logger.info(f"✅ Created queue state record {queue_state_id} for file {file.filename}")
                    
                except Exception as queue_err:
                    logger.error(f"❌ Failed to handle queue/database operations: {queue_err}")
                    # Even if queue fails, blob is already uploaded. 
                    # We might want to notify user or handle it.

                return {
                    'message': f"File '{file.filename}' uploaded successfully",
                    'file_path': f"{username}/{file.filename}",
                    'filename': file.filename,
                    "code": 200
                }
            except Exception as e:
                logger.error(f"❌ Azure upload failed: {str(e)}")
                return {'msg': f"Azure upload failed: {str(e)}", "code": 417}

        return {'msg': 'Invalid file type', "code": 400}

    def put(self):
        """ファイルの更新/置換"""
        args_parser = FileParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        file = args.get("file")
        if not username:
            raise messages.UserNameNotExistsError
        if file and self.allowed_file(file.filename):
            try:
                # 检查文件是否存在
                blob_client = current_app.container_client.get_blob_client(f"{username}/{file.filename}")
                
                # 上传文件（覆盖现有文件）
                blob_client.upload_blob(file.stream, overwrite=True)

                logger.info(f"File '{file.filename}' updated successfully")
                return {
                    'message': f"File '{file.filename}' updated successfully",
                    'file_path': f"{username}/{file.filename}",
                    'filename': file.filename,  # 明确返回文件名
                    'original_filename': file.filename,  # 原始文件名
                    'secure_filename': secure_filename(file.filename),  # 安全文件名
                    "code": 200
                }
            except Exception as e:
                return {'msg': f"Azure upload failed: {str(e)}", "code": 417}

        return {'msg': 'Invalid file type', "code": 400}

    def delete(self):
        args_parser = FileDelete()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        filename = args.get("filename")
        if not username:
            raise messages.UserNameNotExistsError
        if filename:
            try:
                # 1. Delete Blob
                blob_client = current_app.container_client.get_blob_client(f"{username}/{filename}")
                blob_client.delete_blob()
                logger.info(f"user: {username}\n option: File '{filename}' deleted from blob successfully")
                
                # 2. Delete Queue Message and Cosmos Record
                try:
                    QueueState.delete_by_filename(username, filename)
                    logger.info(f"✅ Deleted queue records and messages for user {username}, file {filename}")
                except Exception as q_err:
                    logger.error(f"❌ Failed to delete queue records for file {filename}: {q_err}")

                return {"msg": f"{filename}ファイルの削除が成功しました", "code": 200}
            except Exception as e:
                return {"msg": f"{filename}ファイルが存在しないか、削除された", "code": 200}
        else:
            try:
                # Delete all blobs for user
                blobs_to_delete = list(current_app.container_client.list_blobs(name_starts_with=username))
                if blobs_to_delete:
                    current_app.container_client.delete_blobs(*[blob.name for blob in blobs_to_delete])
                    
                    # Also delete all queue records/messages for this user
                    # We can iterate through blobs or just call a general delete for the user
                    for blob in blobs_to_delete:
                        # Extract just the filename from blob.name (which is "username/filename")
                        fname = blob.name.split("/")[-1] if "/" in blob.name else blob.name
                        try:
                            QueueState.delete_by_filename(username, fname)
                        except:
                            pass
                            
                    return {"msg": "すべてのファイルの削除が成功しました", "code": 200}
                else:
                    return {"msg": "ファイルが存在しないか、削除された", "code": 200}
            except Exception as e:
                return {"msg": str(e), "code": 200}
                # return {"msg": "ファイルが存在しないか、削除された", "code": 200}

    @staticmethod
    def allowed_file(filename):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

class CheckToken(GlobalResource):
    """检查文件token数量的类"""
    
    def post(self):
        """检查文件token数量"""
        args_parser = CheckTokenParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        attachment_names = args.get("attachment_names")
        deploy_model = args.get("deploy_model", "gpt-4o")  # 默认使用gpt-4o
        
        if not username:
            raise messages.UserNameNotExistsError
            
        if not attachment_names or not isinstance(attachment_names, list):
            return {"error": "Invalid attachment_names", "code": 400}
        
        try:
            models = []
            if isinstance(deploy_model, str):
                norm = deploy_model.strip().lower()
                if norm in ("both", "all"):
                    models = ["gpt-4o", "gpt-5.2"]
                elif "," in norm:
                    models = [m.strip() for m in norm.split(",") if m.strip()]
                else:
                    models = [deploy_model]
            elif isinstance(deploy_model, (list, tuple)):
                models = list(deploy_model)
            else:
                models = ["gpt-4o"]

            if len(models) == 1:
                model = models[0]
                result = cal_tokens(username, attachment_names, model)
                return {
                    "total_tokens": result.get("total_tokens", 0),
                    "file_tokens": result.get("file_tokens", {}),
                    "success": result.get("success", False),
                    "deploy_model": model,
                    "code": 200
                }
            else:
                results_by_model = {}
                for model in models:
                    try:
                        results_by_model[model] = cal_tokens(username, attachment_names, model)
                    except Exception as inner_e:
                        results_by_model[model] = {
                            "error": str(inner_e),
                            "total_tokens": 0,
                            "file_tokens": {},
                            "success": False
                        }
                return {
                    "results_by_model": results_by_model,
                    "deploy_model": models,
                    "success": True,
                    "code": 200
                }
        except Exception as e:
            return {
                "error": str(e),
                "total_tokens": 0,
                "success": False,
                "code": 500
            }

system_api.add_resource(SessionManagement, "/session_management")
system_api.add_resource(FileManagement, "/upload_file")
system_api.add_resource(CheckToken, "/check_token")
