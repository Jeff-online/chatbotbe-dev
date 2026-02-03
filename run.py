import os
from app import create_app

web_app = create_app(os.getenv('FLASK_ENV') or 'default')


if __name__ == '__main__':
    web_app.run(host="0.0.0.0")

