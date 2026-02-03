from auth import token_auth
from flask_restful import Resource
from flask_restful import fields, marshal_with


user_fields = {
    'id': fields.Integer,
    'username': fields.String,
}


class GlobalResource(Resource):
    method_decorators = [token_auth.login_required]


class SuccessResource(Resource):
    method_decorators = [token_auth.login_required, marshal_with(user_fields)]

