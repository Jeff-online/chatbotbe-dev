import logging
from app import messages
from authlib.jose import jwt
from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from . import wuser_api
from .args_parser import *
from datetime import datetime, timedelta
from common.common_resource import GlobalResource, Resource


class LoginApi(Resource):

    def post(self):
        args_parser = UserArgsParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        if not username:
            raise messages.UserNameNotExistsError
        password = args.get("password")
        if not password:
            raise messages.PasswordNotExistsError
        user_data = list(current_app.container.query_items(
            query=f"SELECT * FROM users u WHERE u.username = '{username}'",
            enable_cross_partition_query=True
        ))
        if user_data:
            user_info = user_data[0]
            if not user_info.get("user_status"):
                raise messages.LoginError
            if not check_password_hash(user_info['password'], password):
                raise messages.PasswordError
            token = self.generate_auth_token(user_id=user_info["id"], exp=datetime.now() + timedelta(seconds=10800))
            logging.info(f"{username} Login Successful")
            return {'token': token.decode('ascii'), 'duration': 10800, 'username': user_info["username"], 
                    'success': True, 'permission': user_info["permission"], 'UserId': user_info["id"]}
        raise messages.UserNotExistsError

    @staticmethod
    def generate_auth_token(user_id, exp):
        header = {'alg': 'HS256'}
        key = current_app.config['SECRET_KEY']
        data = {'id': str(user_id)}
        data.update({"exp": exp})
        return jwt.encode(header=header, payload=data, key=key)


class UserPassword(GlobalResource):

    def post(self):
        args_parser = UserInfoParser()
        args = args_parser.parser.parse_args()
        username = args.get("username")
        new_password = args.get("new_password")
        if not username:
            raise messages.UserNameNotExistsError
        if not new_password:
            raise messages.PasswordNotExistsError
        is_username = list(current_app.container.query_items(
            query=f"SELECT * FROM user u WHERE u.username = '{username}'",
            enable_cross_partition_query=True
        ))
        if not is_username:
            raise messages.UserNotExistsError
        else:
            user_info = is_username[0]
            user_info["password"] = generate_password_hash(new_password)
            current_app.container.upsert_item(user_info)
            logging.info(f"{username} Password changed")
            return {'msg': 'Password changed', 'code': 200}


wuser_api.add_resource(LoginApi, "/login")
wuser_api.add_resource(UserPassword, "/user_update")