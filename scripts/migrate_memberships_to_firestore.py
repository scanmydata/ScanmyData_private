#!/usr/bin/env python
"""
Migrate group memberships from SQLite to Firestore.
Run this script once to push existing UserGroup records to Firestore.

Usage:
    python scripts/migrate_memberships_to_firestore.py
"""

import os
import sys
import logging

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Set environment variable to enable Firestore
os.environ['FIRESTORE_ENABLED'] = '1'


def migrate_memberships():
    """Migrate all user group memberships from SQLite to Firestore."""
    try:
        from models import db, User, UserGroup, Group
        from admin.firestore_sync import fs_atomic_add_user_to_group, FIRESTORE_ENABLED
        from app import app
        
        if not FIRESTORE_ENABLED:
            logger.error("Firestore is not enabled. Set FIRESTORE_ENABLED=1 in environment.")
            return False
        
        logger.info("Starting migration of group memberships to Firestore...")
        
        with app.app_context():
            # Get all UserGroup records
            user_groups = db.session.query(UserGroup).all()
            logger.info(f"Found {len(user_groups)} user group memberships to migrate")
            
            migrated = 0
            failed = 0
            skipped = 0
            
            for ug in user_groups:
                try:
                    # Get the user
                    user = db.session.query(User).filter_by(id=ug.user_id).first()
                    if not user:
                        logger.warning(f"User {ug.user_id} not found, skipping UserGroup {ug.id}")
                        skipped += 1
                        continue
                    
                    # Skip if no Firebase UID
                    if not user.firebase_uid:
                        logger.debug(f"User {user.username} has no firebase_uid, skipping UserGroup {ug.id}")
                        skipped += 1
                        continue
                    
                    # Get the group
                    group = db.session.query(Group).filter_by(id=ug.group_id).first()
                    if not group:
                        logger.warning(f"Group {ug.group_id} not found, skipping UserGroup {ug.id}")
                        skipped += 1
                        continue
                    
                    # Get the role (default to 'member')
                    role = ug.role or 'member'
                    
                    # Migrate to Firestore
                    result = fs_atomic_add_user_to_group(user.firebase_uid, group.name, role)
                    if result:
                        logger.info(f"✓ Migrated {user.username} to group '{group.name}' as {role}")
                        migrated += 1
                    else:
                        logger.error(f"✗ Failed to migrate {user.username} to group '{group.name}'")
                        failed += 1
                
                except Exception as e:
                    logger.error(f"Error migrating UserGroup {ug.id}: {e}")
                    failed += 1
            
            # Summary
            logger.info("")
            logger.info("=" * 60)
            logger.info(f"Migration Summary:")
            logger.info(f"  Total memberships: {len(user_groups)}")
            logger.info(f"  Successfully migrated: {migrated}")
            logger.info(f"  Failed: {failed}")
            logger.info(f"  Skipped: {skipped}")
            logger.info("=" * 60)
            
            if failed > 0:
                logger.warning(f"Migration completed with {failed} errors")
                return False
            else:
                logger.info("Migration completed successfully!")
                return True
    
    except Exception as e:
        logger.exception(f"Fatal error during migration: {e}")
        return False


if __name__ == '__main__':
    success = migrate_memberships()
    sys.exit(0 if success else 1)
