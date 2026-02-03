import os
import logging
from opencensus.ext.azure.log_exporter import AzureLogHandler

base_path = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or "@^4_00wedv**pi)+(!w1rwi=d3q4l=ie=g-u$s8jevmj*zgg2h"
    INIT_PW = "123456"

    log_level = logging.INFO
    logger = logging.getLogger()
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )

    APP_INSIGHTS_CONNECTION_STRING = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    ai_handler = AzureLogHandler(
        connection_string=APP_INSIGHTS_CONNECTION_STRING
    )
    ai_handler.setFormatter(formatter)
    ai_handler.setLevel(log_level)

    logger.addHandler(ai_handler)
    logger.setLevel(log_level)

    ALLOWED_EXTENSIONS = {'pdf', 'xlsx','txt', 'xls', 'json', 'docx', 'jpg', 'jpeg', 'png'}


class DevelopmentConfig(Config):
    COSMOS_URI = "https://ailab-db.documents.azure.com:443/"
    DATABASE_NAME = "chatbot_test1"
    CONTAINER_NAME = "TestContainer"
    DEBUG = True  # True


class ProductionConfig(Config):
    DEBUG = False


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
