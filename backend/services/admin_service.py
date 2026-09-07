import os
import json
from typing import List, Dict, Any, Optional, Set
from datetime import datetime
from .supabase_client import get_supabase_client
from .schema_manager import SchemaManager
from .async_utils import run_blocking
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

class AdminService:
    """Service for managing admin chunking workflow"""
    
    def __init__(self):
        self.supabase = get_supabase_client()
        self.schema_manager = SchemaManager()
    
    async def add_to_admin_queue(self, user_id: str, document_id: str, 
                                filename: str, file_size: int, storage_url: str, priority: int = 1, codebook: str = "AS3000"):
        """Add document to admin processing queue"""
        try:
            queue_data = {
                "user_id": user_id,
                "document_id": document_id,
                "codebook": codebook,
                "filename": filename,
                "file_size": file_size,
                "storage_url": storage_url,
                "priority": priority,
                "status": "pending"
            }

            # supabase-py is sync — offload so this does not block the event loop
            # while other uploads / dashboard polls are in flight.
            result = await run_blocking(
                lambda: self.supabase.table('admin_queue').insert(queue_data).execute()
            )

            # Send email notification to admin (SMTP is sync; the helper offloads it).
            await self.send_admin_notification(user_id, filename, document_id)

            return result.data[0] if result.data else None

        except Exception as e:
            print(f"Error adding to admin queue: {e}")
            raise Exception(f"Failed to add document to admin queue: {str(e)}")
    
    async def get_pending_documents(self, admin_user_id: Optional[str] = None, 
                                   limit: int = 50, offset: int = 0):
        """Get all pending documents for admin processing"""
        try:
            query = self.supabase.table('admin_queue').select('*')
            
            if admin_user_id:
                query = query.eq('assigned_admin_id', admin_user_id)
            else:
                query = query.eq('status', 'pending')
            
            result = query.order('priority', desc=True).order('created_at', desc=False).range(offset, offset + limit - 1).execute()
            return result.data
            
        except Exception as e:
            print(f"Error getting pending documents: {e}")
            raise Exception(f"Failed to get pending documents: {str(e)}")
    
    async def assign_document_to_admin(self, queue_id: str, admin_user_id: str):
        """Assign a document to an admin for processing"""
        try:
            result = self.supabase.table('admin_queue').update({
                "assigned_admin_id": admin_user_id,
                "status": "assigned",
                "updated_at": "now()"
            }).eq('id', queue_id).execute()
            
            return result.data[0] if result.data else None
            
        except Exception as e:
            print(f"Error assigning document: {e}")
            raise Exception(f"Failed to assign document: {str(e)}")
    
    async def upload_refined_chunks(self, user_id: str, document_id: str, 
                                  chunks: List[Dict], admin_user_id: str, admin_notes: str = ""):
        """Upload admin-refined chunks to user account"""
        try:
            # Update document status in user's schema
            await self.schema_manager.update_user_document(user_id, document_id, {
                'admin_status': 'completed',
                'admin_processed_at': 'now()',
                'admin_user_id': admin_user_id,
                'refined_chunk_count': len(chunks),
                'status': 'ready_for_search',
                'admin_notes': admin_notes
            })
            
            # Insert chunks into user's schema
            chunk_data = []
            for i, chunk in enumerate(chunks):
                chunk_data.append({
                    'document_id': document_id,
                    'chunk_index': i,
                    'text': chunk['text'],
                    'clause_number': chunk.get('clause_number'),
                    'page_number': chunk.get('page_number'),
                    'entities': chunk.get('entities', []),
                    'detailed_analysis': chunk.get('detailed_analysis', {}),
                    'metadata': chunk.get('metadata', {})
                })
            
            if chunk_data:
                await self.schema_manager.insert_user_chunks(user_id, chunk_data)
            
            # Update admin queue
            self.supabase.table('admin_queue').update({
                'status': 'completed',
                'assigned_admin_id': admin_user_id,
                'admin_notes': admin_notes
            }).eq('document_id', document_id).execute()
            
            # Record in admin history
            admin_history_data = {
                'admin_user_id': admin_user_id,
                'document_id': document_id,
                'user_id': user_id,
                'filename': await self.get_document_filename(document_id),
                'chunks_created': len(chunks),
                'admin_notes': admin_notes
            }
            
            self.supabase.table('admin_processing_history').insert(admin_history_data).execute()
            
            return {"status": "success", "chunks_uploaded": len(chunks)}
            
        except Exception as e:
            print(f"Error uploading refined chunks: {e}")
            raise Exception(f"Failed to upload refined chunks: {str(e)}")
    
    async def get_document_filename(self, document_id: str) -> str:
        """Get filename for a document"""
        try:
            result = self.supabase.table('documents').select('filename').eq('id', document_id).single().execute()
            return result.data['filename'] if result.data else "Unknown"
        except:
            return "Unknown"
    
    async def send_admin_notification(self, user_id: str, filename: str, document_id: str):
        """Send email notification to admin about new document"""
        try:
            # Get user info (offloaded to thread pool — sync HTTP under the hood)
            user_info = await run_blocking(
                lambda: self.supabase.table('profiles').select('email, full_name').eq('id', user_id).single().execute()
            )
            
            if not user_info.data:
                print(f"User info not found for user_id: {user_id}")
                return
            
            user_email = user_info.data['email']
            user_name = user_info.data.get('full_name', 'Unknown User')
            
            # Email configuration (you'll need to set these in your environment)
            admin_email = os.getenv('ADMIN_EMAIL', 'admin@yourcompany.com')
            smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
            smtp_port = int(os.getenv('SMTP_PORT', '587'))
            smtp_username = os.getenv('SMTP_USERNAME', '')
            smtp_password = os.getenv('SMTP_PASSWORD', '')
            app_name = os.getenv('APP_NAME', 'AS/NZS AI Code Assistant')
            
            # Create email content
            subject = f"[{app_name}] New Document Upload - {filename}"
            body = f"""
            New document uploaded for processing:
            
            User: {user_name} ({user_email})
            File: {filename}
            Document ID: {document_id}
            Upload Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            
            Please review and chunk this document for optimal search results.
            
            Admin Dashboard: {os.getenv('FRONTEND_URL', 'http://localhost:3000')}/admin
            """
            
            # Send email
            if smtp_username and smtp_password:
                await self._send_email(admin_email, subject, body, smtp_server, smtp_port, smtp_username, smtp_password)
            else:
                print(f"Email notification (SMTP not configured): {subject}")
                print(f"Body: {body}")
                
        except Exception as e:
            print(f"Error sending admin notification: {e}")
    
    async def _send_email(self, to_email: str, subject: str, body: str,
                         smtp_server: str, smtp_port: int, username: str, password: str):
        """Send email using SMTP (offloaded so the event loop stays free)."""

        def _do_send() -> None:
            msg = MIMEMultipart()
            msg['From'] = username
            msg['To'] = to_email
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            server = smtplib.SMTP(smtp_server, smtp_port, timeout=15)
            try:
                server.starttls()
                server.login(username, password)
                server.sendmail(username, to_email, msg.as_string())
            finally:
                try:
                    server.quit()
                except Exception:
                    pass

        try:
            await run_blocking(_do_send)
            print(f"Email sent successfully to {to_email}")
        except Exception as e:
            print(f"Error sending email: {e}")
    
    async def get_admin_stats(self, admin_user_id: Optional[str] = None):
        """Get admin statistics matching AdminStatsResponse schema."""
        try:
            # Fetch aggregate counts expected by AdminStatsResponse
            total_users = self.supabase.table('profiles').select('id', count='exact').execute()
            total_documents = self.supabase.table('documents').select('id', count='exact').execute()
            pending_documents = self.supabase.table('documents').select('id', count='exact').eq('status', 'pending').execute()
            completed_documents = self.supabase.table('documents').select('id', count='exact').eq('status', 'ready_for_search').execute()
            total_chunks = self.supabase.table('chunks').select('id', count='exact').execute()

            # Supabase python client returns count on response.count when count='exact'
            response = {
                'total_users': getattr(total_users, 'count', 0) or 0,
                'total_documents': getattr(total_documents, 'count', 0) or 0,
                'pending_documents': getattr(pending_documents, 'count', 0) or 0,
                'completed_documents': getattr(completed_documents, 'count', 0) or 0,
                'total_chunks': getattr(total_chunks, 'count', 0) or 0,
            }

            return response

        except Exception as e:
            print(f"Error getting admin stats: {e}")
            raise Exception(f"Failed to get admin stats: {str(e)}")

    async def get_document_registry(self, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        """Return registry data for all documents with owner, queue, version, and duplicate insights."""
        try:
            documents_resp = (
                self.supabase
                .table('documents')
                .select('*')
                .order('created_at', desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )
            documents = documents_resp.data or []

            if not documents:
                return {
                    "summary": {
                        "total_documents": 0,
                        "unique_users": 0,
                        "ready_documents": 0,
                        "pending_documents": 0,
                        "duplicate_groups": 0,
                        "total_duplicates": 0,
                    },
                    "documents": [],
                }

            user_ids = list({doc.get('user_id') for doc in documents if doc.get('user_id')})
            doc_ids = [doc['id'] for doc in documents]
            filenames = list({doc.get('filename') for doc in documents if doc.get('filename')})

            profiles_map: Dict[str, Dict[str, Any]] = {}
            if user_ids:
                profiles_resp = (
                    self.supabase
                    .table('profiles')
                    .select('id, email, full_name, role')
                    .in_('id', user_ids)
                    .execute()
                )
                if profiles_resp.data:
                    profiles_map = {profile['id']: profile for profile in profiles_resp.data}

            queue_map: Dict[str, Dict[str, Any]] = {}
            if doc_ids:
                queue_resp = (
                    self.supabase
                    .table('admin_queue')
                    .select('*')
                    .in_('document_id', doc_ids)
                    .execute()
                )
                if queue_resp.data:
                    queue_map = {item['document_id']: item for item in queue_resp.data}

            # Fetch related documents for duplicate/version insights
            if filenames:
                related_docs_resp = (
                    self.supabase
                    .table('documents')
                    .select('*')
                    .in_('filename', filenames)
                    .execute()
                )
                related_docs = related_docs_resp.data or []
            else:
                related_docs = documents

            version_groups: Dict[str, List[Dict[str, Any]]] = {}
            duplicate_groups: Dict[str, List[Dict[str, Any]]] = {}

            processed_duplicate_keys = set()

            for doc in related_docs:
                version_key = f"{doc.get('user_id')}::{doc.get('filename')}"
                version_groups.setdefault(version_key, []).append(doc)

                dup_key = f"{doc.get('filename')}::{doc.get('file_size') or 0}"
                duplicate_groups.setdefault(dup_key, []).append(doc)

            registry_entries: List[Dict[str, Any]] = []
            duplicate_group_count = 0
            duplicate_total_docs = 0

            ready_count = 0
            pending_count = 0

            for doc in documents:
                if doc.get('status') == 'ready_for_search':
                    ready_count += 1
                else:
                    pending_count += 1

                owner = profiles_map.get(doc.get('user_id'), {})
                queue = queue_map.get(doc['id'])

                version_key = f"{doc.get('user_id')}::{doc.get('filename')}"
                versions = version_groups.get(version_key, [])
                versions_sorted = sorted(
                    versions,
                    key=lambda item: item.get('created_at') or '',
                    reverse=True,
                )
                version_payload = [
                    {
                        "document_id": item['id'],
                        "created_at": item.get('created_at'),
                        "status": item.get('status'),
                        "admin_status": item.get('admin_status'),
                        "is_current": item['id'] == doc['id'],
                    }
                    for item in versions_sorted
                ]

                dup_key = f"{doc.get('filename')}::{doc.get('file_size') or 0}"
                duplicates = duplicate_groups.get(dup_key, [])
                duplicate_count = len(duplicates)
                if duplicate_count > 1 and dup_key not in processed_duplicate_keys:
                    duplicate_group_count += 1
                    duplicate_total_docs += duplicate_count
                    processed_duplicate_keys.add(dup_key)

                registry_entries.append(
                    {
                        "id": doc["id"],
                        "user_id": doc.get("user_id"),
                        "filename": doc.get("filename"),
                        "codebook": doc.get("codebook"),
                        "discipline": doc.get("discipline"),
                        "status": doc.get("status"),
                        "admin_status": doc.get("admin_status"),
                        "chunk_count": doc.get("chunk_count"),
                        "refined_chunk_count": doc.get("refined_chunk_count"),
                        "file_size": doc.get("file_size"),
                        "storage_url": doc.get("storage_url"),
                        "processing_model": doc.get("processing_model"),
                        "created_at": doc.get("created_at"),
                        "updated_at": doc.get("updated_at"),
                        "admin_processed_at": doc.get("admin_processed_at"),
                        "owner": {
                            "id": doc.get("user_id"),
                            "email": owner.get("email"),
                            "full_name": owner.get("full_name"),
                            "role": owner.get("role"),
                        },
                        "queue": {
                            "status": queue.get("status") if queue else None,
                            "priority": queue.get("priority") if queue else None,
                            "admin_notes": queue.get("admin_notes") if queue else None,
                            "assigned_admin_id": queue.get("assigned_admin_id") if queue else None,
                            "created_at": queue.get("created_at") if queue else None,
                            "updated_at": queue.get("updated_at") if queue else None,
                        }
                        if queue
                        else None,
                        "duplicate_key": dup_key,
                        "duplicate_count": duplicate_count,
                        "versions": version_payload,
                    }
                )

            summary = {
                "total_documents": len(documents),
                "unique_users": len(user_ids),
                "ready_documents": ready_count,
                "pending_documents": pending_count,
                "duplicate_groups": duplicate_group_count,
                "total_duplicates": max(0, duplicate_total_docs - duplicate_group_count),
            }

            return {
                "summary": summary,
                "documents": registry_entries,
            }

        except Exception as exc:
            print(f"Error building document registry: {exc}")
            raise Exception(f"Failed to build document registry: {str(exc)}")

    async def get_project_overview(self) -> Dict[str, Any]:
        """Return system-wide overview stats with full Supabase auth user list."""
        try:
            # Fetch all auth users via admin API (service role required)
            auth_users: List[Dict[str, Any]] = []
            page = 1

            while True:
                resp = self.supabase.auth.admin.list_users(page=page, per_page=1000)
                users_batch: List[Any] = []
                next_page: Optional[int] = None

                if isinstance(resp, dict):
                    users_batch = resp.get("users", [])
                    next_page = resp.get("nextPage")
                else:
                    users_batch = getattr(resp, "users", []) or []
                    next_token = getattr(resp, "next_page_token", None)
                    if next_token is not None:
                        try:
                            next_page = int(next_token)
                        except (TypeError, ValueError):
                            next_page = None

                if not users_batch:
                    break

                for user in users_batch:
                    if isinstance(user, dict):
                        auth_users.append(user)
                    else:
                        # Convert dataclass / type to dict fallback
                        user_dict = getattr(user, "__dict__", {}) or {}
                        auth_users.append(user_dict)

                if not next_page:
                    break
                page = next_page

            # Map profiles for richer metadata
            profiles_resp = self.supabase.table('profiles').select('id, email, full_name, role, created_at').execute()
            profiles = profiles_resp.data or []
            profiles_map: Dict[str, Dict[str, Any]] = {profile['id']: profile for profile in profiles}
            
            # Get subscription information
            PLAN_DISPLAY_NAMES = {
                'sole_trader_free': 'Sole Trader',
                'individual_monthly': 'Individual',
                'professional': 'Professional'
            }
            
            # Get individual subscriptions (all subscriptions are now individual)
            subscriptions_resp = self.supabase.table('user_subscriptions').select('user_id, plan_name, status, subscription_type').eq('subscription_type', 'individual').execute()
            subscriptions = subscriptions_resp.data or []
            subscription_map: Dict[str, Dict[str, Any]] = {}
            
            # Map individual subscriptions
            for sub in subscriptions:
                user_id = sub.get('user_id')
                if user_id:
                    plan_name = sub.get('plan_name', '')
                    subscription_map[user_id] = {
                        'plan_name': plan_name,
                        'plan_display_name': PLAN_DISPLAY_NAMES.get(plan_name, plan_name.replace('_', ' ').title()) if plan_name else None,
                        'status': sub.get('status')
                    }
            
            # Company subscriptions removed - all subscriptions are now individual

            # Gather documents metadata
            documents_resp = (
                self.supabase
                .table('documents')
                .select('id, user_id, chunk_count, status, created_at, updated_at')
                .execute()
            )
            documents = documents_resp.data or []

            total_documents = len(documents)
            total_chunks = sum(doc.get('chunk_count') or 0 for doc in documents)

            # Build per-user document stats
            user_docs_map: Dict[str, Dict[str, Any]] = {}
            total_ready = 0
            total_pending = 0

            for doc in documents:
                user_id = doc.get('user_id')
                if not user_id:
                    continue

                stats = user_docs_map.setdefault(
                    user_id,
                    {
                        "document_count": 0,
                        "chunk_count": 0,
                        "last_activity": None,
                        "statuses": set(),  # type: ignore[arg-type]
                    },
                )
                stats["document_count"] += 1
                stats["chunk_count"] += doc.get('chunk_count') or 0

                doc_status = doc.get('status')
                if doc_status:
                    cast_statuses: Set[str] = stats["statuses"]
                    cast_statuses.add(doc_status)
                    if doc_status == 'ready_for_search':
                        total_ready += 1
                    elif doc_status in ('pending_admin_review', 'admin_processing', 'pending'):
                        total_pending += 1

                doc_timestamp = doc.get('updated_at') or doc.get('created_at')
                if doc_timestamp:
                    last_activity = stats["last_activity"]
                    if not last_activity or doc_timestamp > last_activity:
                        stats["last_activity"] = doc_timestamp

            # Build final user list merging auth and document info
            overview_users: List[Dict[str, Any]] = []

            for auth_user in auth_users:
                user_dict = auth_user if isinstance(auth_user, dict) else getattr(auth_user, "__dict__", {}) or {}
                user_id = user_dict.get('id')
                if not user_id:
                    continue

                profile = profiles_map.get(user_id, {})
                doc_stats = user_docs_map.get(
                    user_id,
                    {
                        "document_count": 0,
                        "chunk_count": 0,
                        "last_activity": None,
                        "statuses": set(),
                    },
                )

                user_metadata = user_dict.get('user_metadata') or {}
                email = user_dict.get('email') or profile.get('email')
                full_name = (
                    profile.get('full_name')
                    or user_metadata.get('full_name')
                    or (email.split('@')[0] if email and '@' in email else email)
                    or 'Unknown User'
                )

                created_at = user_dict.get('created_at') or profile.get('created_at')
                last_sign_in = user_dict.get('last_sign_in_at')
                last_activity = doc_stats["last_activity"] or last_sign_in or created_at

                # Get subscription info for this user
                subscription_info = subscription_map.get(user_id, {})
                subscription_type = subscription_info.get('plan_display_name') or subscription_info.get('plan_name') or None
                
                overview_users.append(
                    {
                        "id": user_id,
                        "email": email,
                        "full_name": full_name,
                        "role": profile.get('role', user_metadata.get('role', 'user')),
                        "created_at": created_at,
                        "last_activity": last_activity,
                        "document_count": doc_stats["document_count"],
                        "chunk_count": doc_stats["chunk_count"],
                        "statuses": sorted(doc_stats["statuses"]),
                        "subscription_type": subscription_type,
                    }
                )

            # Include users that only exist in documents/profiles but not currently active in auth (edge cases)
            for user_id, stats in user_docs_map.items():
                if any(user["id"] == user_id for user in overview_users):
                    continue

                profile = profiles_map.get(user_id, {})
                # Get subscription info for this user
                subscription_info = subscription_map.get(user_id, {})
                subscription_type = subscription_info.get('plan_display_name') or subscription_info.get('plan_name') or None
                
                overview_users.append(
                    {
                        "id": user_id,
                        "email": profile.get('email', f"user:{user_id[:8]}@unknown"),
                        "full_name": profile.get('full_name', f'User {user_id[:8]}'),
                        "role": profile.get('role', 'user'),
                        "created_at": profile.get('created_at'),
                        "last_activity": stats["last_activity"],
                        "document_count": stats["document_count"],
                        "chunk_count": stats["chunk_count"],
                        "statuses": sorted(stats["statuses"]),
                        "subscription_type": subscription_type,
                    }
                )

            overview_users.sort(key=lambda item: item["document_count"], reverse=True)

            stats_payload = {
                "total_users": len(overview_users),
                "total_documents": total_documents,
                "total_chunks": total_chunks,
                "active_users": sum(1 for user in overview_users if user["document_count"] > 0),
                "ready_documents": total_ready,
                "pending_documents": total_pending,
            }

            return {
                "stats": stats_payload,
                "users": overview_users,
            }

        except Exception as exc:
            print(f"Error building project overview: {exc}")
            raise Exception(f"Failed to build project overview: {str(exc)}")
