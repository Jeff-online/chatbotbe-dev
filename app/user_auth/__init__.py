from app import messages
from flask import Blueprint
from flask_restful import Api

wuser = Blueprint("wuser", __name__)
wuser_api = Api(wuser, errors=messages.errors)

from . import log_out