import os
import openai
from flask import Flask
from flask_cors import *
from config import config
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient, PartitionKey


def create_app(config_name=None):
    if config_name is None:
        config_name = os.getenv('FLASK_CONFIG', 'development')
    app = Flask(__name__)

    CORS(app, supports_credentials=True, resources={
        r"*": {
            "origins": "*"
        }
    })

    with app.app_context():
        app.config.from_object(config[config_name])
        register_extensions(app)
        register_blueprints(app)
        # register_commands(app)
        return app


def register_extensions(app):
    credential = DefaultAzureCredential()
    openai_token = credential.get_token(os.getenv("SCOPE"))
    client = CosmosClient(app.config["COSMOS_URI"], credential=credential)
    database_name = app.config['DATABASE_NAME']
    container_name = app.config['CONTAINER_NAME']
    database = client.create_database_if_not_exists(id=database_name)
    container = database.create_container_if_not_exists(
        id=container_name,
        partition_key=PartitionKey(path="/id")
    )
    container_c = database.create_container_if_not_exists(
        id="history_document",
        partition_key=PartitionKey(path="/session_id")
    )
    blob_service_client = BlobServiceClient(account_url=os.getenv("ACCOUNT_URL"), credential=credential)
    container_client = blob_service_client.get_container_client(os.getenv("STORAGE_CONTAINER_NAME"))
    app.cosmos_client = client
    app.container = container
    app.container_c = container_c

    openai.api_type = "azure_ad"
    openai.api_base = os.getenv("AZURE_OPENAI_ENDPOINT")
    openai.api_version = os.getenv("AZURE_OPENAI_API_VERSION")
    deployment_id = os.getenv("AZURE_OPENAI_MODEL")

    app.openai = openai
    app.credential = credential
    app.openai_token = openai_token.token
    app.token_expires = openai_token.expires_on
    app.deployment_id = deployment_id

    app.container_client = container_client


def register_blueprints(app):
    prefix = '/dev-api'
    from .user_auth import wuser
    app.register_blueprint(wuser, url_prefix=prefix)
    from .system import wsystem
    app.register_blueprint(wsystem, url_prefix=prefix)




