import six
from flask import current_app
from flask_restful import reqparse, abort


class MyArgument(reqparse.Argument):

    def handle_validation_error(self, error, bundle_errors):
        error_str = six.text_type(error)
        error_msg = self.help.format(error_msg=error_str) if self.help else error_str
        msg = [error_msg]

        if current_app.config.get("BUNDLE_ERRORS", False) or bundle_errors:
            return error, msg
        abort(400, message=error_msg)


class BaseArgsParser:
    def __init__(self):
        self.parser = reqparse.RequestParser(argument_class=MyArgument)


