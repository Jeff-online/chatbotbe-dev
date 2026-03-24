import os
import openai
import logging
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
    logger = logging.getLogger(__name__)
    credential = DefaultAzureCredential()
    openai_token = credential.get_token(os.getenv("SCOPE"))
    client = CosmosClient(app.config["COSMOS_URI"], credential=credential)
    database_name = app.config['DATABASE_NAME']
    container_name = app.config['CONTAINER_NAME']
    queue_state_container_name = app.config.get('QUEUE_STATE_CONTAINER_NAME', 'task_queue')
    database = client.create_database_if_not_exists(id=database_name)
    container = database.create_container_if_not_exists(
        id=container_name,
        partition_key=PartitionKey(path="/id")
    )
    container_task_queue = database.create_container_if_not_exists(
        id=queue_state_container_name,
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
    app.container_task_queue = container_task_queue

    try:
        # 配置 Azure OpenAI
        logger.info("正在配置 Azure OpenAI...")
        openai.api_type = "azure_ad"
        
        # 获取 GPT-5 配置
        gpt5_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        gpt5_version = os.getenv("AZURE_OPENAI_API_VERSION")
        gpt5_deployment = os.getenv("AZURE_OPENAI_MODEL")
        
        # 验证必填的环境变量
        if not gpt5_endpoint:
            logger.error("缺少环境变量 AZURE_OPENAI_ENDPOINT")
            raise ValueError("缺少环境变量 AZURE_OPENAI_ENDPOINT")
        if not gpt5_version:
            logger.error("缺少环境变量 AZURE_OPENAI_API_VERSION")
            raise ValueError("缺少环境变量 AZURE_OPENAI_API_VERSION")
        if not gpt5_deployment:
            logger.error("缺少环境变量 AZURE_OPENAI_MODEL")
            raise ValueError("缺少环境变量 AZURE_OPENAI_MODEL")
        
        # 获取 GPT-4o 配置 (可选，默认使用 GPT-5 配置)
        gpt4o_endpoint = os.getenv("AZURE_OPENAI_GPT4O_ENDPOINT") or gpt5_endpoint
        gpt4o_version = os.getenv("AZURE_OPENAI_GPT4O_API_VERSION") or gpt5_version
        gpt4o_deployment = os.getenv("AZURE_OPENAI_GPT4O_DEPLOYMENT") or gpt5_deployment
        
        # 设置 OpenAI API 参数
        openai.api_base = gpt5_endpoint
        openai.api_version = gpt5_version
        deployment_id = gpt5_deployment
        
        # 保存模型配置
        app.model_configs = {
            "gpt-5": {"endpoint": gpt5_endpoint, "api_version": gpt5_version, "deployment": gpt5_deployment},
            "gpt-4o": {"endpoint": gpt4o_endpoint, "api_version": gpt4o_version, "deployment": gpt4o_deployment},
        }
        app.default_model = "gpt-5"
        
        # 记录配置信息
        logger.info(f"GPT-5 配置：endpoint={gpt5_endpoint}, version={gpt5_version}, deployment={gpt5_deployment}")
        logger.info(f"GPT-4o 配置：endpoint={gpt4o_endpoint}, version={gpt4o_version}, deployment={gpt4o_deployment}")
        logger.info(f"默认模型：{app.default_model}")
        logger.info("Azure OpenAI 配置完成")
        
    except ValueError as ve:
        logger.error(f"OpenAI 配置错误 - 环境变量验证失败：{str(ve)}")
        raise
    except Exception as e:
        logger.error(f"OpenAI 配置过程中发生异常：{str(e)}", exc_info=True)
        raise

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




