import azure.functions as func

import os
import time
import json

from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.kusto.ingest import KustoIngestClient, IngestionProperties, FileDescriptor, BlobDescriptor, DataFormat, ReportLevel, ReportMethod
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential

import logging

if os.environ.get('DEBUG').lower() == "yes":
    is_ingest_data_flush_immediately = True
    # adx will instage event immediately, but will impact performance if the traffic loading is too high
else:
    is_ingest_data_flush_immediately = False

CONTAINER = os.environ.get('CONTAINER')
KUSTO_DATABASE = os.environ.get('KUSTO_DATABASE')

"""
connect to keyvault and get key pair
"""
keyVaultName = os.environ.get('KEY_VAULT_NAME')
event_topic_storage_name = os.environ.get('STORAGE_NAME')
authority_id = os.environ.get('AZURE_TENANT_ID')

KVUri = f"https://{keyVaultName}.vault.azure.net"
credential = DefaultAzureCredential()
client = SecretClient(vault_url=KVUri, credential=credential)

# ADX
retrieved_secret = client.get_secret(event_topic_storage_name)
event_topic_storage_key = retrieved_secret.value

client_id = event_topic_storage_name
client_secret = event_topic_storage_key
cluster_ingest_URI = "https://ingest-warriortwadxstg01.southeastasia.kusto.windows.net"

kcsb_ingest = KustoConnectionStringBuilder.with_aad_application_key_authentication(
    cluster_ingest_URI, client_id, client_secret, authority_id)
ADX_INGESTION_CLIENT = KustoIngestClient(kcsb_ingest)

# blob map to adx
blob_adx_name = "win_eda_datasource"


def ingest_blob_to_adx(file_process_storage_name, blob_path, filesize):
    
    file_process_storage_retrieved_secret = client.get_secret(file_process_storage_name+"saskey1")
    file_process_storage_key = file_process_storage_retrieved_secret.value

    table = blob_adx_name
    ingest_mapping = f"json_mapping_{table}"

    ingest_configuration = IngestionProperties(database=KUSTO_DATABASE, table=table, dataFormat=DataFormat.MULTIJSON,
                                               ingestion_mapping_reference=ingest_mapping, report_level=ReportLevel.FailuresAndSuccesses, flush_immediately=is_ingest_data_flush_immediately)

    blob_fullpath = blob_path + file_process_storage_key
    blob_data_description = BlobDescriptor(blob_fullpath, filesize)

    try:
        ADX_INGESTION_CLIENT.ingest_from_blob(
            blob_data_description, ingestion_properties=ingest_configuration)

        logging.info("setup adx ingest config, url:%s , database:%s , table:%s",
                 cluster_ingest_URI, KUSTO_DATABASE, table)
        logging.info("setup blob data source, url:%s",
                 blob_fullpath)
    except Exception as error:
        logging.error(f"BlobError: {error}")


def main(event: func.EventGridEvent):
    result = json.dumps({
        'id': event.id,
        'data': event.get_json(),
        'topic': event.topic,
        'subject': event.subject,
        'event_type': event.event_type,
    })

    logging.info(f"Python EventGrid trigger processed an event: {result}")

    """
    purge_local_data
    """
    numdays = 86400*3  # days
    now = time.time()
    for dirpath, dirnames, filenames in os.walk("spearman"):
        for f in filenames:
            current_check_path = os.path.join(dirpath, f)
            timestamp = os.path.getmtime(current_check_path)
            if now-numdays > timestamp:
                logging.info(f"remove: {current_check_path}")
                os.remove(current_check_path)

    """
    ingest data from blob to ADX
    """
    storage_account = event.topic[event.topic.rindex("/")+1:]
    ingest_blob_to_adx(file_process_storage_name=storage_account,
                       blob_path=event.get_json()['url'],
                       filesize=event.get_json()['contentLength'])
