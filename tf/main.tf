# Terraform configuration for the Air AI Platform Proof of Concept

provider "aws" {
  region = "us-west-2"
}

locals {
  prefix = "air-ai-poc"
  lambda_zip_path = "${path.module}/lambda_packages"
  common_tags = {
    Project     = "Air AI Platform"
    Environment = "poc"
    ManagedBy   = "Terraform"
  }
}

#########################
# S3 Bucket for Assets
#########################

resource "aws_s3_bucket" "assets" {
  bucket = "${local.prefix}-assets"
  force_destroy = true  # For easier cleanup in PoC (remove in production)
  
  tags = local.common_tags
}

resource "aws_s3_bucket_notification" "bucket_notification" {
  bucket = aws_s3_bucket.assets.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.s3_event_trigger.arn
    events              = ["s3:ObjectCreated:*"]
  }

  depends_on = [aws_lambda_permission.allow_bucket]
}

#########################
# VPC and Networking
#########################

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "rds" {
  name        = "${local.prefix}-rds-sg"
  description = "Allow access to PostgreSQL"
  vpc_id      = data.aws_vpc.default.id
  
  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }
  
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  
  tags = local.common_tags
}

#########################
# DynamoDB for Status
#########################

resource "aws_dynamodb_table" "processing_status" {
  name           = "${local.prefix}-processing-status"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "assetId"
  range_key      = "featureType"

  attribute {
    name = "assetId"
    type = "S"
  }

  attribute {
    name = "featureType"
    type = "S"
  }
  
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"

  tags = local.common_tags
}

#########################
# SQS Queue
#########################

resource "aws_sqs_queue" "processing_queue" {
  name                      = "${local.prefix}-processing-queue"
  delay_seconds             = 0
  max_message_size          = 262144
  message_retention_seconds = 86400
  receive_wait_time_seconds = 10
  visibility_timeout_seconds = 300
  
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.processing_dlq.arn
    maxReceiveCount     = 3
  })
  
  tags = local.common_tags
}

resource "aws_sqs_queue" "processing_dlq" {
  name = "${local.prefix}-processing-dlq"
  
  tags = local.common_tags
}

#########################
# RDS PostgreSQL
#########################

resource "aws_db_subnet_group" "postgres" {
  name       = "${local.prefix}-db-subnet-group"
  subnet_ids = slice(tolist(data.aws_subnets.default.ids), 0, 2)
  
  tags = local.common_tags
}

resource "aws_db_parameter_group" "postgres" {
  name   = "${local.prefix}-postgres-pg"
  family = "postgres13"
  
  parameter {
    name  = "shared_preload_libraries"
    value = "pg_stat_statements"
  }
  
  tags = local.common_tags
}

resource "aws_db_instance" "postgres" {
  identifier             = "${local.prefix}-postgres"
  engine                 = "postgres"
  engine_version         = "13.7"
  instance_class         = "db.t3.micro"
  allocated_storage      = 20
  storage_type           = "gp2"
  
  db_name                = "aiplatform"
  username               = "ai_admin"
  password               = "Change-Me-In-Production!"  # In production, use AWS Secrets Manager
  
  parameter_group_name   = aws_db_parameter_group.postgres.name
  db_subnet_group_name   = aws_db_subnet_group.postgres.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  
  skip_final_snapshot    = true
  
  tags = local.common_tags
}

#########################
# SSM Parameters
#########################

resource "aws_ssm_parameter" "db_host" {
  name  = "/${local.prefix}/db/host"
  type  = "String"
  value = aws_db_instance.postgres.address
  
  tags = local.common_tags
}

resource "aws_ssm_parameter" "db_port" {
  name  = "/${local.prefix}/db/port"
  type  = "String"
  value = "5432"
  
  tags = local.common_tags
}

resource "aws_ssm_parameter" "db_name" {
  name  = "/${local.prefix}/db/name"
  type  = "String"
  value = aws_db_instance.postgres.db_name
  
  tags = local.common_tags
}

resource "aws_ssm_parameter" "db_username" {
  name  = "/${local.prefix}/db/username"
  type  = "String"
  value = aws_db_instance.postgres.username
  
  tags = local.common_tags
}

resource "aws_ssm_parameter" "db_password" {
  name  = "/${local.prefix}/db/password"
  type  = "SecureString"
  value = aws_db_instance.postgres.password
  
  tags = local.common_tags
}

#########################
# IAM Roles & Policies
#########################

resource "aws_iam_role" "lambda_role" {
  name = "${local.prefix}-lambda-role"
  
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
  
  tags = local.common_tags
}

resource "aws_iam_policy" "lambda_policy" {
  name = "${local.prefix}-lambda-policy"
  
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Effect   = "Allow"
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
          "s3:GetObjectAttributes"
        ]
        Effect   = "Allow"
        Resource = [
          aws_s3_bucket.assets.arn,
          "${aws_s3_bucket.assets.arn}/*"
        ]
      },
      {
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query"
        ]
        Effect   = "Allow"
        Resource = aws_dynamodb_table.processing_status.arn
      },
      {
        Action = [
          "ssm:GetParameters"
        ]
        Effect   = "Allow"
        Resource = [
          "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${local.prefix}/db/*"
        ]
      },
      {
        Action = [
          "states:StartExecution"
        ]
        Effect   = "Allow"
        Resource = aws_sfn_state_machine.asset_processing.arn
      },
      {
        Action = [
          "sqs:SendMessage",
          "sqs:GetQueueAttributes"
        ]
        Effect   = "Allow"
        Resource = aws_sqs_queue.processing_queue.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_policy_attachment" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = aws_iam_policy.lambda_policy.arn
}

resource "aws_iam_role_policy_attachment" "lambda_vpc_policy" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role" "step_functions_role" {
  name = "${local.prefix}-step-functions-role"
  
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
      }
    ]
  })
  
  tags = local.common_tags
}

resource "aws_iam_policy" "step_functions_policy" {
  name = "${local.prefix}-step-functions-policy"
  
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "lambda:InvokeFunction"
        ]
        Effect   = "Allow"
        Resource = [
          aws_lambda_function.metadata_extraction.arn,
          aws_lambda_function.update_status.arn
        ]
      },
      {
        Action = [
          "sqs:SendMessage",
          "sqs:GetQueueAttributes"
        ]
        Effect   = "Allow"
        Resource = aws_sqs_queue.processing_queue.arn
      },
      {
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ]
        Effect   = "Allow"
        Resource = aws_dynamodb_table.processing_status.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "step_functions_policy_attachment" {
  role       = aws_iam_role.step_functions_role.name
  policy_arn = aws_iam_policy.step_functions_policy.arn
}

#########################
# Lambda Functions
#########################

# Archive files for Lambda functions
data "archive_file" "s3_event_trigger" {
  type        = "zip"
  source_file = "${local.lambda_zip_path}/s3_event_trigger_lambda.py"
  output_path = "${local.lambda_zip_path}/s3_event_trigger_lambda.zip"
}

data "archive_file" "metadata_extraction" {
  type        = "zip"
  source_file = "${local.lambda_zip_path}/metadata_extraction_lambda.py"
  output_path = "${local.lambda_zip_path}/metadata_extraction_lambda.zip"
}

data "archive_file" "update_status" {
  type        = "zip"
  source_file = "${local.lambda_zip_path}/update_status_lambda.py"
  output_path = "${local.lambda_zip_path}/update_status_lambda.zip"
}

# S3 Event Trigger Lambda
resource "aws_lambda_function" "s3_event_trigger" {
  function_name     = "${local.prefix}-s3-event-trigger"
  filename          = data.archive_file.s3_event_trigger.output_path
  source_code_hash  = data.archive_file.s3_event_trigger.output_base64sha256
  role              = aws_iam_role.lambda_role.arn
  handler           = "s3_event_trigger_lambda.lambda_handler"
  runtime           = "python3.9"
  timeout           = 30
  memory_size       = 256
  
  environment {
    variables = {
      STEP_FUNCTION_ARN = aws_sfn_state_machine.asset_processing.arn
    }
  }
  
  tags = local.common_tags
}

# Metadata Extraction Lambda
resource "aws_lambda_function" "metadata_extraction" {
  function_name     = "${local.prefix}-metadata-extraction"
  filename          = data.archive_file.metadata_extraction.output_path
  source_code_hash  = data.archive_file.metadata_extraction.output_base64sha256
  role              = aws_iam_role.lambda_role.arn
  handler           = "metadata_extraction_lambda.lambda_handler"
  runtime           = "python3.9"
  timeout           = 30
  memory_size       = 256
  
  vpc_config {
    subnet_ids         = slice(tolist(data.aws_subnets.default.ids), 0, 2)
    security_group_ids = [aws_security_group.rds.id]
  }
  
  tags = local.common_tags
  
  # Allow time for the VPC ENI creation
  depends_on = [aws_iam_role_policy_attachment.lambda_vpc_policy]
}

# Update Status Lambda
resource "aws_lambda_function" "update_status" {
  function_name     = "${local.prefix}-update-status"
  filename          = data.archive_file.update_status.output_path
  source_code_hash  = data.archive_file.update_status.output_base64sha256
  role              = aws_iam_role.lambda_role.arn
  handler           = "update_status_lambda.lambda_handler"
  runtime           = "python3.9"
  timeout           =