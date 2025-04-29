import json
import boto3
import logging
import os
from datetime import datetime
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS services
dynamodb = boto3.resource('dynamodb')

def lambda_handler(event, context):
    """
    Updates the processing status in DynamoDB
    This is a simple Lambda that creates/updates status entries
    """
    logger.info(f"Received event: {json.dumps(event)}")
    
    # Extract parameters from the event
    asset_id = event.get('assetId')
    feature_type = event.get('featureType')
    status = event.get('status')
    status_details = event.get('statusDetails', {})
    
    # Validate required parameters
    if not asset_id or not feature_type or not status:
        error_msg = 'Missing required parameters: assetId, featureType, and status are required'
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    try:
        # Get the DynamoDB table name from environment variables
        table_name = os.environ['PROCESSING_STATUS_TABLE']
        table = dynamodb.Table(table_name)
        
        # Prepare timestamp
        timestamp = datetime.utcnow().isoformat()
        
        # Try to get existing item
        try:
            response = table.get_item(
                Key={
                    'assetId': asset_id,
                    'featureType': feature_type
                }
            )
            
            item_exists = 'Item' in response
        except ClientError as e:
            logger.error(f"Error checking if item exists: {str(e)}")
            item_exists = False
        
        if item_exists:
            # Update existing item
            response = table.update_item(
                Key={
                    'assetId': asset_id,
                    'featureType': feature_type
                },
                UpdateExpression='SET #status = :status, statusDetails = :statusDetails, updatedAt = :updatedAt',
                ExpressionAttributeNames={
                    '#status': 'status'
                },
                ExpressionAttributeValues={
                    ':status': status,
                    ':statusDetails': status_details,
                    ':updatedAt': timestamp
                },
                ReturnValues='ALL_NEW'
            )
            logger.info(f"Updated status for asset {asset_id}, feature {feature_type}")
            updated_item = response.get('Attributes', {})
        else:
            # Create new item
            item = {
                'assetId': asset_id,
                'featureType': feature_type,
                'status': status,
                'statusDetails': status_details,
                'createdAt': timestamp,
                'updatedAt': timestamp
            }
            
            table.put_item(Item=item)
            logger.info(f"Created new status entry for asset {asset_id}, feature {feature_type}")
            updated_item = item
        
        # Return the updated status info
        return {
            'assetId': asset_id,
            'featureType': feature_type,
            'status': status,
            'statusDetails': status_details,
            'updatedAt': timestamp,
            'createdAt': updated_item.get('createdAt', timestamp)
        }
    except Exception as e:
        logger.error(f"Error updating status: {str(e)}")
        raise