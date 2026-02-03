import re
from app import messages


class InfoAuth:

    @classmethod
    def validate_username(cls, value):
        if not value:
            return value
        if not re.match(r'^[0-9A-Za-z_@.-]+$', value):
            raise messages.UserNameError
        return value



