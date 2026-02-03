from werkzeug.exceptions import HTTPException

errors = {
    "UserNameError": {
        'message': "ユーザー名の形式が間違っています。数字、アルファベット、アンダースコア、@、.だけです。",
        "status": 400
    },
    "PhoneDigitsTypeError": {
        'message': "電話番号の桁数をご確認ください",
        "status": 400
    },
    "PhoneFigureTypeError": {
        'message': "電話番号は数字で構成する",
        "status": 400
    },
    "PhonePrefixTypeError": {
        'message': "誤った電話番号",
        "status": 400
    },
    "EmailTypeError": {
        'message': "メール書式エラー",
        "status": 400
    },
    'IpFormatError': {
        'message': "メール書式エラー",
        'status': 400
    },
    'PortFormatError': {
        'message': "ポートフォーマットエラー",
        'status': 400
    },
    'TimeFormatError': {
        'message': "時間フォーマットエラー",
        'status': 400
    },
    'TimeRangeError': {
        'message': "誤ったタイムフレーム",
        'status': 400
    },
    "RolePermissionError": {
        'message': 'ロール許可エラー',
        'status': 400
    },
    "UserLimitLoginIp": {
        "message": "このユーザは、指定されたIPからのみログインできる。",
        "status": 400
    },
    'UserTokenNotExpired': {
        'message': "ユーザ・トークンの有効期限切れ",
        'status': 401
    },
    'UserTokenNotValid': {
        'message': "ユーザ・トークンが不正",
        'status': 401
    },
    'InvalidParamError': {
        'message': "禁止 無効なパラメータ",
        'status': 403
    },
    'UserNotExistsError': {
        'message': "ユーザーが存在しない",
        'status': 409
    },
    'FileNotExistsError': {
        'message': "ファイルが存在しない",
        'status': 409
    },
    'SessionIdNotExistsError': {
        'message': "セッションが存在しない",
        'status': 409
    },
    'UserMaxCountError': {
        'message': "失敗 最大許容ユーザ数を超えた",
        'status': 409
    },
    'UserNameNotExistsError': {
        'message': "ユーザー名を入力してください",
        'status': 409
    },
    'NickIdNotExistsError': {
        'message': "ユーザーIDを入力してください。",
        'status': 409
    },
    'PasswordNotExistsError': {
        'message': "パスワードを入力してください",
        'status': 409
    },
    'PasswordError': {
        'message': "不正なパスワード",
        'status': 409
    },
    'UserAlreadyExistsError': {
        'message': "ユーザー名が既に存在する",
        'status': 409
    },
    'NickIdAlreadyExistsError': {
        'message': "ユーザーIDが既に存在する",
        'status': 409
    },
    "LoginError": {
        'message': "ログイン失敗 ユーザーがロックアウトされた",
        "status": 410
    },
    'SerializationError': {
        'message': "内部サーバーエラー シリアル化の失敗",
        'status': 500
    },
    'InterfaceCallError': {
        'message': "実装されていないインターフェースの呼び出しに失敗",
        'status': 501
    },
}


class UserLimitLoginIp(HTTPException):
    code = 400


class RolePermissionError(HTTPException):
    code = 400


class TimeRangeError(HTTPException):
    code = 400


class TimeFormatError(HTTPException):
    code = 400


class PortFormatError(HTTPException):
    code = 400


class IpFormatError(HTTPException):
    code = 400


class EmailTypeError(HTTPException):
    code = 400


class PhonePrefixTypeError(HTTPException):
    code = 400


class PhoneDigitsTypeError(HTTPException):
    code = 400


class PhoneFigureTypeError(HTTPException):
    code = 400


class UserNameError(HTTPException):
    code = 400


class UserTokenNotExpired(HTTPException):
    code = 401


class UserTokenNotValid(HTTPException):
    code = 401


class InvalidParamError(HTTPException):
    code = 403


class UserNotExistsError(HTTPException):
    code = 409


class FileNotExistsError(HTTPException):
    code = 409


class SessionIdNotExistsError(HTTPException):
    code = 409


class UserMaxCountError(HTTPException):
    code = 409


class UserNameNotExistsError(HTTPException):
    code = 409


class PasswordNotExistsError(HTTPException):
    code = 409


class NickIdNotExistsError(HTTPException):
    code = 409


class PasswordError(HTTPException):
    code = 409


class UserAlreadyExistsError(HTTPException):
    code = 409


class NickIdAlreadyExistsError(HTTPException):
    code = 409


class LoginError(HTTPException):
    code = 410


class SerializationError(HTTPException):
    code = 500


class InterfaceCallError(HTTPException):
    code = 501




