from app import messages
from flask import Blueprint
from flask_restful import Api

wsystem = Blueprint("wsystem", __name__)
system_api = Api(wsystem, errors=messages.errors)

from . import user, homepage