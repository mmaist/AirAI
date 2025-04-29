import json
import boto3
import urllib.parse
import logging
import os
import uuid
import time

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS services
step_functions = boto3.client('stepfunctions')

def lambda_handler(event, context):
    """
    Lambda function triggered by S3 events
    This starts the Step Functions workflow for asset processing
    """
    logger.info(f"Received event: {json.dumps(event)}")
    
    results = []
    
    # Process each record from the S3 event
    for record in event.get('Records', []):
        # Ensure this is an S3 object created event
        if record.get('eventSource') != 'aws:s3' or not record.get('eventName', '').startswith('ObjectCreated'):
            logger.info('Skipping non-S3 ObjectCreated event')
            continue
        
        # Extract S3 details
        bucket = record['s3']['bucket']['name']
        key = urllib.parse.unquote_plus(record['s3']['object']['key'])
        
        # Skip files in processed or results folders
        if key.startswith('processed/') or key.startswith('results/'):
            logger.info(f"Skipping processing for {key} - in processed or results folder")
            continue
        
        # Skip certain file types (e.g., system files, temporary files)
        if should_skip_processing(key):
            logger.info(f"Skipping processing for {key} - excluded file type")
            continue
        
        # Generate a unique asset ID
        asset_id = f"asset-{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
        
        # Prepare input for Step Functions
        step_functions_input = {
            'assetId': asset_id,
            'bucket': bucket,
            'key': key,
            'eventTime': record.get('eventTime'),
            'eventSource': 'S3'
        }
        
        # Start Step Functions workflow
        try:
            step_function_arn = os.environ['STEP_FUNCTION_ARN']
            response = step_functions.start_execution(
                stateMachineArn=step_function_arn,
                name=f"{asset_id}-processing",
                input=json.dumps(step_functions_input)
            )
            
            logger.info(f"Step Functions execution started: {response['executionArn']}")
            
            results.append({
                'assetId': asset_id,
                'executionArn': response['executionArn'],
                'bucket': bucket,
                'key': key
            })
        except Exception as e:
            logger.error(f"Error starting Step Functions execution for {bucket}/{key}: {str(e)}")
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': f"Processed {len(results)} of {len(event.get('Records', []))} S3 events",
            'results': results
        })
    }

def should_skip_processing(key):
    """
    Determines if file should be skipped for processing
    """
    # Skip hidden files
    if any(part.startswith('.') for part in key.split('/')):
        return True
    
    # Skip temporary files
    if key.endswith('.tmp') or key.endswith('.temp'):
        return True
    
    # Skip system files
    if key.endswith('.DS_Store') or '.Thumbs.db' in key:
        return True
    
    # Skip specific file types you don't want to process
    skip_extensions = ['.lock', '.part', '.crdownload']
    if any(key.endswith(ext) for ext in skip_extensions):
        return True
    
    return False