from common.common_validate import InfoAuth
from werkzeug.datastructures import FileStorage
from common.common_args_parser import BaseArgsParser


class UserManageArgsParser(BaseArgsParser):
    def __init__(self):
        super().__init__()
        self.parser.add_argument('username', type=self.check_username, required=True, help="ユーザー名には数字、アルファベット、アンダースコア、「@」、「. 」、「-」を使用できます")

    @staticmethod
    def check_username(value):
        return InfoAuth.validate_username(value)


class UserParserView(BaseArgsParser):
    def __init__(self):
        super().__init__()
        self.parser.add_argument('nick_id', type=str, location='args')
        self.parser.add_argument('username', type=str, help="ユーザー名の長さは8～30桁", location='args')
        self.parser.add_argument('permission', help="ユーザー権限を確認してください", type=str, location='args')
        self.parser.add_argument('user_status', help="ユーザーステータスを確認してください", type=str, location='args')


class UserNewParser(UserManageArgsParser):
    def __init__(self):
        super().__init__()
        self.parser.add_argument('nick_id', type=str, required=True)
        self.parser.add_argument('permission', required=True, help="ユーザー権限を確認してください", type=str)
        self.parser.add_argument('user_status', required=True, help="ユーザーステータスを確認してください", type=bool)


class UserModifyParser(UserNewParser):
    def __init__(self):
        super().__init__()
        self.parser.add_argument('id', type=str)


class UserDeleteParser(BaseArgsParser):
    def __init__(self):
        super().__init__()
        self.parser.add_argument('id', type=str, location='args')


class SessionParser(BaseArgsParser):
    def __init__(self):
        super().__init__()
        self.parser.add_argument('session_id', type=str, location='args')
        self.parser.add_argument('username', type=str, required=True, help="ユーザー名の長さは8～30桁", location='args')


class SessionAddParser(UserManageArgsParser):
    def __init__(self):
        super().__init__()


class SessionPutParser(SessionAddParser):
    def __init__(self):
        super().__init__()
        self.parser.add_argument('prompt_name', type=str, help="プロンプトファイル名")
        self.parser.add_argument('attachment_name', type=str, action="append", help="添付ファイル名")
        self.parser.add_argument('content', type=str, help="セッションの内容をご確認ください")
        self.parser.add_argument('session_id', type=str)


class FileParser(BaseArgsParser):
    def __init__(self):
        super().__init__()
        self.parser.add_argument('username', type=str, location='form', required=True)
        self.parser.add_argument('file', type=FileStorage, location="files", required=True)


class FileDelete(BaseArgsParser):
    def __init__(self):
        super().__init__()
        self.parser.add_argument('username', type=str, location='args', required=True)
        self.parser.add_argument('filename', type=str, location='args')


