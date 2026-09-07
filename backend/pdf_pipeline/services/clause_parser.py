"""
Clause Parser Service
=====================
Parses normalized_document.txt to extract structured clauses with:
1. Skip table of contents (clauses with ... in title or empty bodies)
2. Merge list items (1,2,3,a,b,c,i,ii,iii) into parent clause
3. Clean text and remove noise
"""

import re
import hashlib
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class ClauseParser:
    """Parse clauses from normalized document text"""
    
    def __init__(self):
        self.clause_pattern = re.compile(
            r'\[CLAUSE\]\s+'
            r'Number:\s*([^\n]+)\s+'
            r'Title:\s*([^\n]*?)\s+'
            r'(?:Parent:\s*([^\n]*)\s+)?'
            r'Level:\s*([^\n]+)\s+'
            r'Pages:\s*([^\n]+)\s+'
            r'Confidence:\s*([^\n]+)\s+'
            r'Body:\s*(.*?)(?=\n-{80}|\[CLAUSE\]|$)',
            re.DOTALL
        )
    
    def normalize_clause_number(self, number: str) -> str:
        """Normalize clause number by fixing OCR errors
        
        Handles:
        - Leading asterisks: *2.3.1 -> 2.3.1
        - Spaces in numbers: 2. 3.1 -> 2.3.1
        - Commas instead of dots: 2,3.1 or 2,3,1 -> 2.3.1
        - Mixed issues: *2, 3. 1 -> 2.3.1
        """
        if not number:
            return number
        
        # Remove leading/trailing asterisks and whitespace
        normalized = number.strip().lstrip('*').strip()
        
        # Replace commas with dots
        normalized = normalized.replace(',', '.')
        
        # Remove spaces within the number (but keep spaces before appendix text)
        # For "2. 3. 1" -> "2.3.1"
        if re.match(r'^[\d\s\.]+$', normalized):
            normalized = normalized.replace(' ', '')
        
        # For "Appendix A" keep the space
        # For mixed patterns like "2 .3.1" or "2. 3.1"
        normalized = re.sub(r'(\d)\s*\.\s*(\d)', r'\1.\2', normalized)
        
        return normalized
    
    def generate_clause_id(self, clause_number: str, title: str) -> str:
        """Generate unique clause ID"""
        content = f"{clause_number}_{title}"
        return f"clause_{hashlib.md5(content.encode()).hexdigest()[:8]}"
    
    def is_toc_clause(self, title: str, body: str) -> bool:
        """Check if this is a table of contents entry"""
        # TOC has dots like "SCOPE ..................... 33"
        if '...' in title or '........' in title:
            return True
        return False
    
    def is_list_item_clause(self, clause_number: str) -> bool:
        """Check if clause number is a list item (should be merged into parent)"""
        # Just a number: 1, 2, 3, etc.
        if re.match(r'^\d+$', clause_number):
            return True
        
        # Single letter: a, b, c
        if re.match(r'^[a-z]$', clause_number):
            return True
        
        # Parenthesized: (a), (b), (c)
        if re.match(r'^\([a-z]\)$', clause_number):
            return True
        
        # Roman numerals: i, ii, iii, iv, v
        if re.match(r'^[ivxlcdm]+$', clause_number) and len(clause_number) <= 4:
            return True
        
        # Parenthesized roman: (i), (ii), (iii)
        if re.match(r'^\([ivxlcdm]+\)$', clause_number):
            return True
        
        return False
    
    def clean_text(self, text: str) -> str:
        """Clean text - remove noise, normalize whitespace"""
        if not text:
            return ""
        
        # Remove excessive whitespace
        text = re.sub(r'\s+', ' ', text)
        
        # Remove COPYRIGHT markers
        text = re.sub(r'\bCOPYRIGHT\b', '', text)
        
        # Clean up asterisks (often used as footnote markers)
        # Keep them but normalize
        text = re.sub(r'\s*\*+\s*', ' * ', text)
        
        # Remove multiple spaces
        text = re.sub(r' {2,}', ' ', text)
        
        # Trim
        return text.strip()
    
    def parse_from_text(self, normalized_text: str) -> List[Dict]:
        """
        Parse clauses from normalized_document.txt content
        
        Args:
            normalized_text: Content of normalized_document.txt
            
        Returns:
            List of clause dictionaries with proper content
        """
        logger.info("Starting clause parsing from normalized text")
        
        matches = self.clause_pattern.findall(normalized_text)
        logger.info(f"Found {len(matches)} raw clauses in text")
        
        clauses = []
        list_item_buffer = []  # Buffer for list items to merge
        current_parent = None
        
        for number, title, parent, level, pages, confidence, body in matches:
            number = self.normalize_clause_number(number.strip())
            title = title.strip()
            parent = self.normalize_clause_number(parent.strip()) if parent else None
            body = body.strip()
            
            # Skip TOC entries (dots like "SCOPE ..................... 33")
            if self.is_toc_clause(title, body):
                continue
            
            # Check if this is a list item
            if self.is_list_item_clause(number):
                # Buffer this for merging into parent
                list_item_buffer.append({
                    'number': number,
                    'title': title,
                    'body': body
                })
                continue
            
            # If we have buffered list items and now hit a real clause, merge them
            if list_item_buffer and current_parent:
                # Find the parent clause and append list items to its content
                for clause in reversed(clauses):
                    if clause['clause_number'] == current_parent:
                        # Format list items nicely
                        list_content = "\n\n"
                        for item in list_item_buffer:
                            item_text = f"{item['number']}. {item['title']} {item['body']}".strip()
                            list_content += f"\n{item_text}"
                        
                        logger.info(f"🔧 DEBUG: About to merge list items. Clause keys: {clause.keys()}")
                        clause['body_with_subitems'] += list_content
                        logger.info(f"✅ DEBUG: Successfully merged list items using 'body_with_subitems' field")
                        break
                
                list_item_buffer = []
            
            # Clean the body text
            cleaned_body = self.clean_text(body)
            cleaned_title = self.clean_text(title)
            
            # Build clause object
            clause_obj = {
                'clause_id': self.generate_clause_id(number, cleaned_title),
                'clause_number': number,
                'title': cleaned_title,
                'parent_clause_number': parent if parent else None,
                'parent_clause_id': None,  # Will populate in second pass
                'level': int(level) if level.isdigit() else 1,
                'page_start': pages.split('-')[0].strip() if '-' in pages else pages.strip(),
                'page_end': pages.split('-')[1].strip() if '-' in pages else pages.strip(),
                'body_with_subitems': cleaned_body,  # Match Clause model field name
                'full_normalized_text': cleaned_body,  # Same content for now
                'confidence': confidence
            }
            
            clauses.append(clause_obj)
            current_parent = number
        
        logger.info(f"Parsed {len(clauses)} valid clauses (after filtering TOC and merging list items)")
        
        # Second pass: populate parent_clause_id
        clause_map = {c['clause_number']: c['clause_id'] for c in clauses}
        
        for clause in clauses:
            if clause['parent_clause_number'] and clause['parent_clause_number'] in clause_map:
                clause['parent_clause_id'] = clause_map[clause['parent_clause_number']]
        
        # Stats
        with_content = sum(1 for c in clauses if len(c['body_with_subitems']) > 0)
        substantial_content = sum(1 for c in clauses if len(c['body_with_subitems']) > 100)
        
        logger.info(f"Clause parsing complete:")
        logger.info(f"  Total clauses: {len(clauses)}")
        logger.info(f"  With content: {with_content} ({with_content*100//len(clauses) if clauses else 0}%)")
        logger.info(f"  With >100 chars: {substantial_content} ({substantial_content*100//len(clauses) if clauses else 0}%)")
        
        return clauses
