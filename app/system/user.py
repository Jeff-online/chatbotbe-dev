import uuid
import logging
from . import system_api
from app import messages
from . args_parser import *
from flask import current_app
from datetime import datetime
from common.common_resource import GlobalResource, Resource
from werkzeug.security import generate_password_hash
logger = logging.getLogger(__name__)


class SystemUser(GlobalResource):

    def get(self):
        """ユーザーを見る"""
        args_parser = UserParserView()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        nick_id = args.get("nick_id")
        permission = args.get("permission")
        user_status = args.get("user_status")
        query = "SELECT * FROM user u WHERE 1=1"
        params = []
        if username:
            query += " AND u.username = @username"
            params.append({"name": "@username", "value": username})
        if nick_id:
            query += " AND u.nick_id = @nick_id"
            params.append({"name": "@nick_id", "value": nick_id})
        if permission:
            query += " AND u.permission = @permission"
            params.append({"name": "@permission", "value": permission})
        if user_status:
            user_status = True if user_status == "true" else False
            query += " AND u.user_status = @user_status"
            params.append({"name": "@user_status", "value": user_status})
        user_info = list(current_app.container.query_items(query=query, parameters=params, enable_cross_partition_query=True))
        result = []
        for info in user_info:
            result.append({
                "id": info.get("id", "error_data"),
                "username": info.get("username", "error_data"),
                "nick_id": info.get("nick_id", "error_data"),
                "user_status": info.get("user_status", False),
                "permission": info.get("permission", "error_data")
            })
        return {"user_info": result, "code": 200, "params": params, "user_status": user_status}

    def post(self):
        """登録ユーザー"""
        args_parser = UserNewParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        nick_id = args.get("nick_id")
        if not username:
            raise messages.UserNameNotExistsError
        if not nick_id:
            raise messages.NickIdNotExistsError
        is_username = list(current_app.container.query_items(
            query=f"SELECT * FROM user u WHERE u.username = '{username}'",
            enable_cross_partition_query=True
        ))
        if is_username:
            logger.error('Registration failed, user name already exists')
            raise messages.UserAlreadyExistsError
        is_nick = list(current_app.container.query_items(
            query=f"SELECT * FROM user u WHERE u.nick_id = '{nick_id}'",
            enable_cross_partition_query=True
        ))
        if is_nick:
            logger.error('Registration failed, user id already exists')
            raise messages.NickIdAlreadyExistsError
        self.save_success_register(args)
        logger.info(f'user : {username} Successful registration')
        return {'msg': 'success', 'code': 200}

    def put(self):
        """ユーザーの変更"""
        args_parser = UserModifyParser()
        args = args_parser.parser.parse_args()
        user_id = args.get("id")
        if not user_id:
            raise messages.UserNameNotExistsError
        user_data = list(current_app.container.query_items(
            query=f"SELECT * FROM user u WHERE u.id = '{user_id}'",
            enable_cross_partition_query=True
        ))
        if user_data:
            user_info = user_data[0]
            is_data = dict(filter(lambda x: x[1] is not None, args.items()))
            user_info.update(is_data)
            current_app.container.upsert_item(user_info)
            logger.info("User modified successfully")
        else:
            raise messages.UserNotExistsError
        return {'msg': 'success', 'code': 200}

    def delete(self):
        """ユーザー削除"""
        args_parser = UserDeleteParser()
        args = args_parser.parser.parse_args()
        doc_id = args.get("id")
        user_data = current_app.container.read_item(item=doc_id, partition_key=doc_id)
        if user_data:
            current_app.container.delete_item(item=doc_id, partition_key=doc_id)
            logger.info("User deleted successfully")
            return {'msg': 'success', 'code': 200}
        raise messages.UserNotExistsError

    @staticmethod
    def save_success_register(args):
        password_hash = generate_password_hash(current_app.config["INIT_PW"])
        args.update(create_time=datetime.now().isoformat(), password=password_hash, id=str(uuid.uuid4()))
        current_app.container.create_item(body=args)


class SystemInit(GlobalResource):

    def get(self):
        args_parser = UserDeleteParser()
        args = args_parser.parser.parse_args()
        user_id = args.get("id")
        user_data = current_app.container.read_item(item=user_id, partition_key=user_id)
        if user_data:
            password_hash = generate_password_hash(current_app.config["INIT_PW"])
            user_data.update(password=password_hash)
            current_app.container.upsert_item(user_data)
            logger.info("Password initialized successfully")
            return {'msg': 'success', 'code': 200}
        raise messages.UserNotExistsError


system_api.add_resource(SystemUser, "/user")
system_api.add_resource(SystemInit, "/initialization")


