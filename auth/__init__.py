from app import messages
from flask import g, current_app
from flask_httpauth import HTTPTokenAuth
from authlib.jose import jwt, errors, JoseError

token_auth = HTTPTokenAuth("Bearer")        # "Bearer"


def verify_auth_token(token):
    key = current_app.config['SECRET_KEY']
    try:
        payload_data = jwt.decode(token, key)
        payload_data.validate()
    except errors.ExpiredTokenError:
        raise messages.UserTokenNotExpired
    except JoseError:
        raise messages.UserTokenNotValid
    user_info = current_app.container.read_item(item=payload_data['id'], partition_key=payload_data['id'])
    return user_info


def auth_token(token):
    _user = verify_auth_token(token)
    g.user = _user
    return True


token_auth.verify_token(auth_token)
