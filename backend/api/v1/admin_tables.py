"""Admin endpoints for CSV table upload linked to documents."""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from services.supabase_client import get_supabase_client
from services.pdf_ingest_parsers import parse_uploaded_tables_content
from middleware.subscription_check import check_admin_access

router = APIRouter()


@router.post("/upload-tables")
async def upload_tables(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    document_id: str = Form(...),
    admin_notes: str = Form(""),
    current_admin: str = Depends(check_admin_access),
):
    """Upload processed tables from a CSV file for a specific document."""
    try:
        if not file.filename or not file.filename.endswith(".csv"):
            raise HTTPException(status_code=400, detail="Only CSV files are supported for tables")

        content = await file.read()
        tables_data = await parse_uploaded_tables_content(content, file.filename, document_id, user_id)

        if not tables_data:
            raise HTTPException(status_code=400, detail="No valid tables found in file")

        supabase = get_supabase_client()
        batch_size = 50
        total_uploaded = 0

        for i in range(0, len(tables_data), batch_size):
            batch = tables_data[i : i + batch_size]
            result = supabase.table("standard_tables").insert(batch).execute()
            if not result.data:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to insert tables batch {i // batch_size + 1}",
                )
            total_uploaded += len(batch)

        supabase.table("admin_queue").update(
            {
                "status": "completed",
                "admin_notes": (
                    f"{admin_notes}\n\nProcessed tables uploaded from file: {file.filename}. "
                    f"{total_uploaded} tables added."
                ),
            }
        ).eq("document_id", document_id).execute()

        return {
            "message": f"Successfully uploaded {total_uploaded} tables from file",
            "tables_count": total_uploaded,
            "document_id": document_id,
            "user_id": user_id,
            "filename": file.filename,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to upload tables file: {str(exc)}") from exc
