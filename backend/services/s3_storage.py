import os
import boto3
import re
import uuid
from botocore.client import Config
from botocore.exceptions import ClientError, NoCredentialsError
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

class S3Storage:
    """Service for S3-based storage operations with Supabase"""
    
    def __init__(self):
        self.s3_client = self._create_s3_client()
        self.bucket_name = os.getenv('S3_BUCKET_NAME', 'documents')
    
    def _create_s3_client(self):
        """Create S3 client with Supabase S3 configuration"""
        try:
            # Get Supabase S3 configuration from environment
            supabase_url = os.getenv('SUPABASE_URL')
            s3_access_key = os.getenv('S3_ACCESS_KEY_ID')
            s3_secret_key = os.getenv('S3_SECRET_ACCESS_KEY')
            
            if not supabase_url:
                print("⚠️ Supabase URL missing. Using Supabase storage API fallback.")
                return None
            
            # Extract project reference from Supabase URL
            # URL format: https://<project_ref>.supabase.co
            project_ref = supabase_url.split('//')[1].split('.')[0]
            
            # Create S3 endpoint URL
            endpoint_url = f"https://{project_ref}.supabase.co/storage/v1/s3"
            
            # Get region from environment or default
            region = os.getenv('S3_REGION', 'us-east-1')
            
            print(f"🔍 Creating S3 client with:")
            print(f"   Endpoint: {endpoint_url}")
            print(f"   Project Ref: {project_ref}")
            print(f"   Region: {region}")
            
            # Check if we have proper S3 access keys
            if s3_access_key and s3_secret_key:
                print(f"   Using S3 access keys: {s3_access_key[:10]}...")
                
                # Supabase Storage only accepts AWS Signature Version 4.
                # Without this, boto3 can emit SigV2 URLs (AWSAccessKeyId/Signature/Expires)
                # which Supabase rejects with 403 Forbidden.
                s3_client = boto3.client(
                    's3',
                    endpoint_url=endpoint_url,
                    aws_access_key_id=s3_access_key,
                    aws_secret_access_key=s3_secret_key,
                    region_name=region,
                    config=Config(signature_version='s3v4'),
                )
                print(f"✅ S3 client created with proper S3 credentials (s3v4)")
                return s3_client
            else:
                print(f"⚠️ S3 access keys not found, falling back to Supabase storage API")
                return None
            
        except Exception as e:
            print(f"❌ Error creating S3 client: {e}")
            return None
    
    def upload_file(self, file_path: str, user_id: str, filename: str) -> Optional[str]:
        """
        Upload file to S3 storage using multipart upload for large files
        
        Args:
            file_path: Local path to the file
            user_id: User ID for organizing files
            filename: Original filename
            
        Returns:
            S3 URL of the uploaded file or None if failed
        """
        if not self.s3_client:
            print("❌ S3 client not available")
            return None
        
        try:
            # Include a unique object id so same-name uploads do not overwrite.
            safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "document.pdf").strip("._")
            if not safe_filename:
                safe_filename = "document.pdf"
            s3_key = f"{user_id}/{uuid.uuid4()}-{safe_filename}"
            
            print(f"📤 Uploading to S3: {self.bucket_name}/{s3_key}")
            
            # Get file size to determine upload method
            file_size = os.path.getsize(file_path)
            print(f"📊 File size: {file_size / (1024*1024):.2f} MB")
            
            # Use multipart upload for files larger than 50MB
            if file_size > 50 * 1024 * 1024:  # 50MB threshold
                print(f"🔄 Using multipart upload for large file")
                return self._multipart_upload(file_path, s3_key, file_size)
            else:
                print(f"📤 Using single-part upload for small file")
                return self._single_part_upload(file_path, s3_key)
            
        except Exception as e:
            print(f"❌ Error uploading file to S3: {e}")
            return None
    
    def _single_part_upload(self, file_path: str, s3_key: str) -> Optional[str]:
        """Upload file using single-part upload"""
        try:
            with open(file_path, 'rb') as file:
                self.s3_client.upload_fileobj(
                    file,
                    self.bucket_name,
                    s3_key,
                    ExtraArgs={
                        'ContentType': 'application/pdf',
                        'ACL': 'public-read'
                    }
                )
            
            # Generate S3 URL
            s3_url = f"{self.s3_client.meta.endpoint_url}/{self.bucket_name}/{s3_key}"
            print(f"✅ File uploaded to S3 (single-part): {s3_url}")
            
            return s3_url
            
        except Exception as e:
            print(f"❌ Error in single-part upload: {e}")
            return None
    
    def _multipart_upload(self, file_path: str, s3_key: str, file_size: int) -> Optional[str]:
        """Upload file using multipart upload for large files"""
        try:
            # Initialize multipart upload
            response = self.s3_client.create_multipart_upload(
                Bucket=self.bucket_name,
                Key=s3_key,
                ContentType='application/pdf',
                ACL='public-read'
            )
            
            upload_id = response['UploadId']
            print(f"🔄 Started multipart upload: {upload_id}")
            
            # Upload parts
            part_size = 10 * 1024 * 1024  # 10MB per part
            parts = []
            part_number = 1
            
            with open(file_path, 'rb') as file:
                while True:
                    chunk = file.read(part_size)
                    if not chunk:
                        break
                    
                    print(f"📤 Uploading part {part_number} ({len(chunk)} bytes)")
                    
                    response = self.s3_client.upload_part(
                        Bucket=self.bucket_name,
                        Key=s3_key,
                        PartNumber=part_number,
                        UploadId=upload_id,
                        Body=chunk
                    )
                    
                    parts.append({
                        'ETag': response['ETag'],
                        'PartNumber': part_number
                    })
                    
                    part_number += 1
            
            # Complete multipart upload
            print(f"✅ Completing multipart upload with {len(parts)} parts")
            self.s3_client.complete_multipart_upload(
                Bucket=self.bucket_name,
                Key=s3_key,
                UploadId=upload_id,
                MultipartUpload={'Parts': parts}
            )
            
            # Generate S3 URL
            s3_url = f"{self.s3_client.meta.endpoint_url}/{self.bucket_name}/{s3_key}"
            print(f"✅ File uploaded to S3 (multipart): {s3_url}")
            
            return s3_url
            
        except Exception as e:
            print(f"❌ Error in multipart upload: {e}")
            
            # Try to abort multipart upload if it was started
            try:
                if 'upload_id' in locals():
                    self.s3_client.abort_multipart_upload(
                        Bucket=self.bucket_name,
                        Key=s3_key,
                        UploadId=upload_id
                    )
                    print(f"🔄 Aborted multipart upload: {upload_id}")
            except Exception as abort_error:
                print(f"⚠️ Failed to abort multipart upload: {abort_error}")
            
            return None
    
    def generate_signed_url(self, storage_url: str, expiration: int = 3600) -> Optional[str]:
        """
        Generate a signed URL for accessing a file in S3 storage
        
        Args:
            storage_url: The S3 URL of the file
            expiration: URL expiration time in seconds (default: 1 hour)
            
        Returns:
            Signed URL if successful, None otherwise
        """
        if not self.s3_client:
            print("❌ S3 client not available")
            return None
            
        try:
            # Extract S3 key from storage URL
            # URL format: https://project.supabase.co/storage/v1/s3/bucket/key
            parts = storage_url.split('/storage/v1/s3/')
            if len(parts) != 2:
                print(f"❌ Invalid storage URL format: {storage_url}")
                return None
                
            key_with_bucket = parts[1]
            # Remove bucket name from key (assuming bucket is first part)
            key_parts = key_with_bucket.split('/', 1)
            if len(key_parts) != 2:
                print(f"❌ Invalid key format: {key_with_bucket}")
                return None
                
            s3_key = key_parts[1]  # Everything after the bucket name
            
            print(f"🔍 Generating signed URL for key: {s3_key}")
            
            # Generate signed URL
            signed_url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': s3_key},
                ExpiresIn=expiration
            )
            
            print(f"✅ Generated signed URL: {signed_url[:50]}...")
            return signed_url
            
        except Exception as e:
            print(f"❌ Error generating signed URL: {e}")
            return None
    
    def delete_file(self, storage_url: str) -> bool:
        """
        Delete file from S3 storage
        
        Args:
            storage_url: The S3 URL of the file
            
        Returns:
            True if successful, False otherwise
        """
        if not self.s3_client:
            print("❌ S3 client not available")
            return False
        
        try:
            print(f"🔍 Attempting to delete file from S3: {storage_url}")
            
            # Extract S3 key from URL
            # URL format: https://<project_ref>.supabase.co/storage/v1/s3/bucket/user_id/filename
            url_parts = storage_url.split('/')
            if len(url_parts) < 6:
                print(f"❌ Invalid S3 URL format: {storage_url}")
                return False
            
            # Find the bucket name in the URL
            bucket_index = -1
            for i, part in enumerate(url_parts):
                if part == self.bucket_name:
                    bucket_index = i
                    break
            
            if bucket_index == -1:
                print(f"❌ Could not find bucket name in URL: {storage_url}")
                return False
            
            # Get the key (user_id/filename)
            s3_key = '/'.join(url_parts[bucket_index + 1:])
            print(f"🔍 Extracted S3 key: {s3_key}")
            
            # Delete from S3
            print(f"🔍 Calling S3 delete: {self.bucket_name}/{s3_key}")
            response = self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            
            print(f"🔍 S3 delete response: {response}")
            
            if response.get('ResponseMetadata', {}).get('HTTPStatusCode') in [200, 204]:
                print(f"✅ File successfully deleted from S3: {s3_key}")
                return True
            else:
                print(f"❌ S3 delete failed with response: {response}")
                return False
                
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'NoSuchKey':
                print(f"⚠️ File not found in S3: {storage_url}")
                return True  # File doesn't exist, consider it deleted
            else:
                print(f"❌ S3 client error: {e}")
                return False
        except Exception as e:
            print(f"❌ Error deleting file from S3: {e}")
            return False
    
    def file_exists(self, storage_url: str) -> bool:
        """
        Check if file exists in S3
        
        Args:
            storage_url: The S3 URL of the file
            
        Returns:
            True if file exists, False otherwise
        """
        if not self.s3_client:
            return False
        
        try:
            # Extract S3 key from URL
            url_parts = storage_url.split('/')
            bucket_index = -1
            for i, part in enumerate(url_parts):
                if part == self.bucket_name:
                    bucket_index = i
                    break
            
            if bucket_index == -1:
                return False
            
            s3_key = '/'.join(url_parts[bucket_index + 1:])
            
            # Check if object exists
            self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return True
            
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            else:
                print(f"❌ Error checking file existence: {e}")
                return False
        except Exception as e:
            print(f"❌ Error checking file existence: {e}")
            return False 