import json
import boto3
import psycopg2
import hashlib
import time
import uuid
import logging
from datetime import datetime

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS services
s3 = boto3.client('s3')
ssm = boto3.client('ssm')

# Database connection config cache
db_config = None

def lambda_handler(event, context):
    """
    Extracts metadata from an S3 object and stores it in PostgreSQL
    """
    logger.info(f"Received event: {json.dumps(event)}")
    
    # Get database connection config from SSM Parameter Store (if not already cached)
    global db_config
    if not db_config:
        db_config = get_db_config()
    
    try:
        bucket = event['bucket']
        key = event['key']
        asset_id = event.get('assetId', generate_asset_id())
        
        # Get object metadata from S3
        object_metadata = get_s3_object_metadata(bucket, key)
        
        # Generate content hash for change detection
        content_hash = generate_content_hash(bucket, key)
        
        # Prepare metadata object
        metadata = {
            'assetId': asset_id,
            'bucket': bucket,
            'key': key,
            'contentType': object_metadata.get('ContentType', 'application/octet-stream'),
            'contentLength': object_metadata.get('ContentLength', 0),
            'lastModified': object_metadata.get('LastModified').isoformat() if 'LastModified' in object_metadata else datetime.now().isoformat(),
            'etag': object_metadata.get('ETag', '').strip('"'),
            'contentHash': content_hash,
            'uploadTimestamp': datetime.now().isoformat(),
            'assetType': determine_asset_type(object_metadata.get('ContentType', ''))
        }
        
        # Store metadata in PostgreSQL
        store_metadata_in_postgres(metadata)
        
        # Return the enriched metadata
        event['assetId'] = asset_id
        event['metadata'] = metadata
        return event
        
    except Exception as e:
        logger.error(f"Error processing asset: {str(e)}")
        raise

def get_db_config():
    """
    Retrieves database configuration from SSM Parameter Store
    """
    param_names = [
        '/air-ai-poc/db/host',
        '/air-ai-poc/db/port',
        '/air-ai-poc/db/name',
        '/air-ai-poc/db/username',
        '/air-ai-poc/db/password'
    ]
    
    response = ssm.get_parameters(
        Names=param_names,
        WithDecryption=True
    )
    
    config = {}
    for param in response['Parameters']:
        name = param['Name'].split('/')[-1]
        config[name] = param['Value']
    
    return {
        'host': config.get('host'),
        'port': int(config.get('port', 5432)),
        'database': config.get('name'),
        'user': config.get('username'),
        'password': config.get('password')
    }

def generate_asset_id():
    """
    Generates a unique asset ID
    """
    timestamp = int(time.time() * 1000)
    random_suffix = uuid.uuid4().hex[:8]
    return f"asset_{timestamp}_{random_suffix}"

def get_s3_object_metadata(bucket, key):
    """
    Gets object metadata from S3
    """
    return s3.head_object(Bucket=bucket, Key=key)

def generate_content_hash(bucket, key):
    """
    Generates a content hash for the S3 object
    For larger files, we might want to use the ETag instead or implement streaming hash
    """
    # For this POC, we'll use a simplified approach
    # In production, consider using ETag or a more efficient method for large files
    
    # Get object metadata first to check size
    metadata = s3.head_object(Bucket=bucket, Key=key)
    
    # For small files, we can download and hash
    max_size_for_download = 10 * 1024 * 1024  # 10MB
    
    if metadata['ContentLength'] <= max_size_for_download:
        response = s3.get_object(Bucket=bucket, Key=key)
        content = response['Body'].read()
        return hashlib.sha256(content).hexdigest()
    else:
        # For larger files, use ETag as an approximation
        # Note: ETag is not a true content hash for multipart uploads
        return metadata.get('ETag', '').strip('"')

def determine_asset_type(content_type):
    """
    Determines asset type from content type
    """
    if not content_type:
        return 'other'
    
    content_type = content_type.lower()
    
    if content_type.startswith('image/'):
        return 'image'
    if content_type.startswith('video/'):
        return 'video'
    if content_type.startswith('audio/'):
        return 'audio'
    if 'pdf' in content_type:
        return 'document'
    if content_type.startswith('text/'):
        return 'document'
    if ('application/vnd.openxmlformats-officedocument' in content_type or
        'application/msword' in content_type):
        return 'document'
    
    return 'other'

def store_metadata_in_postgres(metadata):
    """
    Stores metadata in PostgreSQL database
    """
    conn = None
    try:
        # Connect to the database
        conn = psycopg2.connect(**db_config)
        cursor = conn.cursor()
        
        # Create assets table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS assets (
                asset_id VARCHAR(50) PRIMARY KEY,
                bucket VARCHAR(100) NOT NULL,
                key_path TEXT NOT NULL,
                content_type VARCHAR(100),
                content_length BIGINT,
                last_modified TIMESTAMP,
                etag VARCHAR(100),
                content_hash VARCHAR(100),
                upload_timestamp TIMESTAMP,
                asset_type VARCHAR(50),
                metadata JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Insert metadata into the database
        query = '''
            INSERT INTO assets (
                asset_id, bucket, key_path, content_type, content_length, 
                last_modified, etag, content_hash, upload_timestamp, asset_type, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (asset_id) 
            DO UPDATE SET 
                content_type = EXCLUDED.content_type,
                content_length = EXCLUDED.content_length,
                last_modified = EXCLUDED.last_modified,
                etag = EXCLUDED.etag,
                content_hash = EXCLUDED.content_hash,
                upload_timestamp = EXCLUDED.upload_timestamp,
                asset_type = EXCLUDED.asset_type,
                metadata = EXCLUDED.metadata,
                updated_at = CURRENT_TIMESTAMP
        '''
        
        values = (
            metadata['assetId'],
            metadata['bucket'],
            metadata['key'],
            metadata['contentType'],
            metadata['contentLength'],
            metadata['lastModified'],
            metadata['etag'],
            metadata['contentHash'],
            metadata['uploadTimestamp'],
            metadata['assetType'],
            json.dumps(metadata)
        )
        
        cursor.execute(query, values)
        conn.commit()
        
        logger.info(f"Metadata stored in PostgreSQL for asset {metadata['assetId']}")
    except Exception as e:
        logger.error(f"Database error: {str(e)}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()