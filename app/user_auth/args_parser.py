from common.common_validate import InfoAuth
from common.common_args_parser import BaseArgsParser


class UserArgsParser(BaseArgsParser):
    def __init__(self):
        super().__init__()
        self.parser.add_argument('username', type=self.check_username, required=True, help='ユーザー名を確認してください')
        self.parser.add_argument('password', type=str, required=True, help='パスワードをご確認ください')

    @staticmethod
    def check_username(value):
        return InfoAuth.validate_username(value)


class UserInfoParser(BaseArgsParser):
    def __init__(self):
        super().__init__()
        self.parser.add_argument('username', type=str, required=True)
        self.parser.add_argument('new_password', type=str, help='新しいパスワードをご確認ください')

    @staticmethod
    def check_username(value):
        return InfoAuth.validate_username(value)






