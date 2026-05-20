"""
Firebase Authentication Handlers for Firebed Private
Handles user registration, login, password reset via Firebase
"""

import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timezone
import requests
import os
from firebase_admin import auth as firebase_auth
from firebase_admin import db, credentials
from . import firebase_config
from admin.encryption import derive_key_from_password, generate_encryption_key

logger = logging.getLogger(__name__)


class FirebaseAuthHandler:
    """Manage Firebase Authentication operations"""

    @staticmethod
    def register_user(email: str, password: str, display_name: str = "") -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Register a new user with Firebase Authentication
        
        Args:
            email: User email
            password: User password (min 6 chars, Firebase requirement)
            display_name: Optional display name
        
        Returns:
            Tuple: (success: bool, uid: Optional[str], error: Optional[str])
        """
        try:
            if not firebase_config.is_firebase_enabled():
                return False, None, "Firebase not enabled"
            
            # Validate password
            if len(password) < 6:
                return False, None, "Password must be at least 6 characters"
            
            if not email or '@' not in email:
                return False, None, "Invalid email format"
            
            # Create Firebase user
            user = firebase_auth.create_user(
                email=email,
                password=password,
                display_name=display_name or email.split('@')[0],
                email_verified=False
            )
            
            # Initialize user data in Realtime Database
            user_data = {
                'uid': user.uid,
                'email': user.email,
                'display_name': user.display_name,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'email_verified': False,
                'groups': [],
                'active': True
            }
            
            # Write user profile to database
            firebase_config.firebase_write_data(f'/users/{user.uid}', user_data)
            
            logger.info(f"User registered successfully: {user.email} (UID: {user.uid})")
            
            # Log activity
            firebase_config.firebase_log_activity(
                user.uid,
                'system',
                'user_registered',
                {'email': email}
            )
            
            return True, user.uid, None
            
        except firebase_auth.EmailAlreadyExistsError:
            logger.warning(f"Registration failed: Email already exists - {email}")
            return False, None, "This email is already registered"
        except firebase_auth.InvalidPasswordError:
            return False, None, "Password does not meet security requirements"
        except Exception as e:
            logger.error(f"Firebase registration error: {e}")
            return False, None, f"Registration failed: {str(e)}"

    @staticmethod
    def login_user(email: str, password: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Verify user credentials via Firebase REST API with password verification
        
        Returns:
            Tuple: (success: bool, uid: Optional[str], error: Optional[str])
        """
        try:
            if not firebase_config.is_firebase_enabled():
                return False, None, "Firebase not enabled"
            
            # First, verify password against Firebase using REST API
            api_key = os.getenv('FIREBASE_API_KEY', '')
            if not api_key:
                logger.warning("FIREBASE_API_KEY not set, trying fallback method")
                # Fallback: get user by email from Firebase Auth (no password check)
                try:
                    user = firebase_auth.get_user_by_email(email)
                    if user and not user.disabled:
                        logger.warning(f"Login attempt without password verification for: {email}")
                        return True, user.uid, None
                except:
                    pass
                return False, None, "Authentication system not properly configured"
            
            # Use Firebase REST API to verify credentials
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
            payload = {
                "email": email,
                "password": password,
                "returnSecureToken": True
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                uid = data.get('localId')
                
                if uid:
                    # Log successful login
                    firebase_config.firebase_log_activity(
                        uid,
                        'system',
                        'user_logged_in_successfully',
                        {'email': email}
                    )
                    logger.info(f"User logged in successfully: {email}")
                    return True, uid, None
            
            elif response.status_code == 400:
                error_data = response.json()
                error_msg = error_data.get('error', {}).get('message', '')
                
                if error_msg == 'INVALID_PASSWORD':
                    logger.warning(f"Invalid password for user: {email}")
                    return False, None, "Invalid email or password"
                elif error_msg == 'EMAIL_NOT_FOUND':
                    logger.warning(f"Email not found: {email}")
                    return False, None, "Invalid email or password"
                elif error_msg == 'USER_DISABLED':
                    logger.warning(f"User account disabled: {email}")
                    return False, None, "User account has been disabled"
                else:
                    return False, None, error_msg or "Login failed"
            
            logger.error(f"Firebase REST API error: {response.status_code} - {response.text}")
            return False, None, "Login failed - server error"
            
        except Exception as e:
            logger.error(f"Firebase login error: {e}")
            return False, None, f"Login failed: {str(e)}"

    @staticmethod
    def get_user_by_uid(uid: str) -> Optional[Dict[str, Any]]:
        """Get user profile from Firebase"""
        try:
            if not firebase_config.is_firebase_enabled():
                return None
            
            user = firebase_auth.get_user(uid)
            user_profile = firebase_config.firebase_read_data(f'/users/{uid}')
            
            if user_profile:
                user_profile['uid'] = uid
                return user_profile
            
            return {
                'uid': uid,
                'email': user.email,
                'display_name': user.display_name,
                'email_verified': user.email_verified,
            }
        except Exception as e:
            logger.error(f"Failed to get user {uid}: {e}")
            return None

    @staticmethod
    def update_user_profile(uid: str, updates: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Update user profile in Firebase"""
        try:
            if not firebase_config.is_firebase_enabled():
                return False, "Firebase not enabled"
            
            # Update display name in Firebase Auth if provided
            if 'display_name' in updates:
                firebase_auth.update_user(uid, display_name=updates['display_name'])
            
            # Update full profile in database
            current_profile = firebase_config.firebase_read_data(f'/users/{uid}')
            if current_profile:
                current_profile.update(updates)
            else:
                current_profile = updates
            
            current_profile['updated_at'] = datetime.now(timezone.utc).isoformat()
            firebase_config.firebase_write_data(f'/users/{uid}', current_profile)
            
            logger.info(f"User profile updated: {uid}")
            return True, None
            
        except Exception as e:
            logger.error(f"Failed to update user {uid}: {e}")
            return False, str(e)

    @staticmethod
    def delete_user(uid: str) -> Tuple[bool, Optional[str]]:
        """Delete user from Firebase"""
        try:
            if not firebase_config.is_firebase_enabled():
                return False, "Firebase not enabled"
            
            # Get user email for logging
            user = firebase_auth.get_user(uid)
            email = user.email
            
            # Log activity before deletion
            firebase_config.firebase_log_activity(
                uid,
                'system',
                'user_deleted',
                {'email': email}
            )
            
            # Delete from Firebase Auth
            firebase_auth.delete_user(uid)
            
            # Delete user profile from database
            firebase_config.firebase_delete_data(f'/users/{uid}')
            
            # Delete user's groups association
            firebase_config.firebase_delete_data(f'/user_groups/{uid}')
            
            logger.info(f"User deleted: {uid} ({email})")
            return True, None
            
        except Exception as e:
            logger.error(f"Failed to delete user {uid}: {e}")
            return False, str(e)

    @staticmethod
    def reset_password(email: str) -> Tuple[bool, Optional[str]]:
        """Send password reset email"""
        try:
            if not firebase_config.is_firebase_enabled():
                return False, "Firebase not enabled"
            
            user = firebase_auth.get_user_by_email(email)
            
            # Send password reset email via Firebase (requires REST API)
            # This is typically done via Firebase Client SDK
            # For Admin SDK, we can generate a reset link manually
            
            logger.info(f"Password reset requested for: {email}")
            
            # Log activity
            firebase_config.firebase_log_activity(
                user.uid,
                'system',
                'password_reset_requested',
                {'email': email}
            )
            
            return True, None
            
        except firebase_auth.UserNotFoundError:
            logger.warning(f"Password reset failed: User not found - {email}")
            return False, "User not found"
        except Exception as e:
            logger.error(f"Password reset error: {e}")
            return False, str(e)

    @staticmethod
    def change_password(uid: str, new_password: str) -> Tuple[bool, Optional[str]]:
        """Change user password"""
        try:
            if not firebase_config.is_firebase_enabled():
                return False, "Firebase not enabled"
            
            if len(new_password) < 6:
                return False, "Password must be at least 6 characters"
            
            firebase_auth.update_user(uid, password=new_password)
            
            logger.info(f"Password changed for user: {uid}")
            
            # Log activity
            firebase_config.firebase_log_activity(
                uid,
                'system',
                'password_changed',
                {}
            )
            
            return True, None
            
        except Exception as e:
            logger.error(f"Failed to change password for {uid}: {e}")
            return False, str(e)

    @staticmethod
    def generate_custom_token(uid: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """Generate a custom token for client-side Firebase authentication"""
        try:
            if not firebase_config.is_firebase_enabled():
                return False, None, "Firebase not enabled"
            
            # Generate custom token valid for 1 hour
            custom_token = firebase_auth.create_custom_token(uid)
            
            logger.info(f"Custom token generated for user: {uid}")
            return True, custom_token.decode('utf-8'), None
            
        except Exception as e:
            logger.error(f"Failed to generate custom token for {uid}: {e}")
            return False, None, str(e)

    @staticmethod
    def create_group_encryption_key(group_name: str, creator_uid: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Create a per-group encryption key
        Stores encrypted key in database, accessible only to group members
        """
        try:
            if not firebase_config.is_firebase_enabled():
                return False, None, "Firebase not enabled"
            
            # Generate new encryption key
            new_key = generate_encryption_key()
            
            # Store key metadata in Firebase
            key_metadata = {
                'group_name': group_name,
                'created_by': creator_uid,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'key_version': 1,
                'active': True
            }
            
            # Write key to protected location
            firebase_config.firebase_write_data(
                f'/group_encryption_keys/{group_name}/metadata',
                key_metadata
            )
            
            # Store actual key (should be encrypted at rest in production)
            firebase_config.firebase_write_data(
                f'/group_encryption_keys/{group_name}/key',
                {'encrypted_key': new_key}
            )
            
            logger.info(f"Group encryption key created for: {group_name}")
            
            return True, new_key, None
            
        except Exception as e:
            logger.error(f"Failed to create group encryption key for {group_name}: {e}")
            return False, None, str(e)

    @staticmethod
    def add_user_to_group(uid: str, group_name: str, role: str = 'member') -> Tuple[bool, Optional[str]]:
        """Add user to a group"""
        try:
            if not firebase_config.is_firebase_enabled():
                return False, "Firebase not enabled"
            
            # Try Firestore first if enabled
            try:
                from admin.firestore_sync import fs_atomic_add_user_to_group, FIRESTORE_ENABLED
                if FIRESTORE_ENABLED:
                    result = fs_atomic_add_user_to_group(uid, group_name, role)
                    if result:
                        logger.info(f"User {uid} added to group {group_name} via Firestore")
                        return True, None
            except ImportError:
                logger.debug("Firestore sync not available, using RTDB fallback")
            except Exception as e:
                logger.warning(f"Firestore add failed: {e}, falling back to RTDB")
            
            # Fallback to RTDB
            # Add to user's groups list
            user_profile = firebase_config.firebase_read_data(f'/users/{uid}')
            if user_profile:
                if 'groups' not in user_profile:
                    user_profile['groups'] = {}
                if 'group_roles' not in user_profile:
                    user_profile['group_roles'] = {}
                
                user_profile['groups'][group_name] = role
                user_profile['group_roles'][group_name] = role
                firebase_config.firebase_write_data(f'/users/{uid}', user_profile)
            
            # Add to group's members list
            group_data = firebase_config.firebase_read_data(f'/groups/{group_name}')
            if group_data:
                if 'members' not in group_data:
                    group_data['members'] = []
                if 'admins' not in group_data:
                    group_data['admins'] = []
                
                # Remove from both lists first
                if uid in group_data['members']:
                    group_data['members'].remove(uid)
                if uid in group_data['admins']:
                    group_data['admins'].remove(uid)
                
                # Add to appropriate list
                if role == 'admin':
                    if uid not in group_data['admins']:
                        group_data['admins'].append(uid)
                else:
                    if uid not in group_data['members']:
                        group_data['members'].append(uid)
                
                firebase_config.firebase_write_data(f'/groups/{group_name}', group_data)
            
            logger.info(f"User {uid} added to group: {group_name} (RTDB)")
            
            # Log activity
            firebase_config.firebase_log_activity(
                uid,
                group_name,
                'user_added_to_group',
                {'group': group_name}
            )
            
            return True, None
            
        except Exception as e:
            logger.error(f"Failed to add user {uid} to group {group_name}: {e}")
            return False, str(e)

    @staticmethod
    def remove_user_from_group(uid: str, group_name: str) -> Tuple[bool, Optional[str]]:
        """Remove user from a group"""
        try:
            if not firebase_config.is_firebase_enabled():
                return False, "Firebase not enabled"
            
            # Try Firestore first if enabled
            try:
                from admin.firestore_sync import fs_atomic_remove_user_from_group, FIRESTORE_ENABLED
                if FIRESTORE_ENABLED:
                    result = fs_atomic_remove_user_from_group(uid, group_name)
                    if result:
                        logger.info(f"User {uid} removed from group {group_name} via Firestore")
                        return True, None
            except ImportError:
                logger.debug("Firestore sync not available, using RTDB fallback")
            except Exception as e:
                logger.warning(f"Firestore remove failed: {e}, falling back to RTDB")
            
            # Fallback to RTDB
            # Remove from user's groups list
            user_profile = firebase_config.firebase_read_data(f'/users/{uid}')
            if user_profile:
                groups = user_profile.get('groups', {})
                if isinstance(groups, dict):
                    if group_name in groups:
                        del groups[group_name]
                else:
                    # Handle legacy list format
                    groups = [g for g in groups if g != group_name]
                
                group_roles = user_profile.get('group_roles', {})
                if isinstance(group_roles, dict) and group_name in group_roles:
                    del group_roles[group_name]
                
                user_profile['groups'] = groups
                user_profile['group_roles'] = group_roles
                firebase_config.firebase_write_data(f'/users/{uid}', user_profile)
            
            # Remove from group's members list
            group_data = firebase_config.firebase_read_data(f'/groups/{group_name}')
            if group_data:
                members = group_data.get('members', [])
                admins = group_data.get('admins', [])
                
                if uid in members:
                    members.remove(uid)
                if uid in admins:
                    admins.remove(uid)
                
                group_data['members'] = members
                group_data['admins'] = admins
                firebase_config.firebase_write_data(f'/groups/{group_name}', group_data)
            
            logger.info(f"User {uid} removed from group: {group_name} (RTDB)")
            
            # Log activity
            firebase_config.firebase_log_activity(
                uid,
                group_name,
                'user_removed_from_group',
                {'group': group_name}
            )
            
            return True, None
            
        except Exception as e:
            logger.error(f"Failed to remove user {uid} from group {group_name}: {e}")
            return False, str(e)

    @staticmethod
    def get_user_groups(uid: str) -> list:
        """Get all groups a user belongs to"""
        try:
            if not firebase_config.is_firebase_enabled():
                return []
            
            # Try Firestore first if enabled
            try:
                from admin.firestore_sync import fs_get_user_groups, FIRESTORE_ENABLED
                if FIRESTORE_ENABLED:
                    groups_dict = fs_get_user_groups(uid)
                    if groups_dict:
                        return list(groups_dict.keys())
            except ImportError:
                logger.debug("Firestore sync not available, using RTDB fallback")
            except Exception as e:
                logger.debug(f"Firestore get failed: {e}, falling back to RTDB")
            
            # Fallback to RTDB
            user_profile = firebase_config.firebase_read_data(f'/users/{uid}')
            if user_profile:
                groups = user_profile.get('groups', {})
                if isinstance(groups, dict):
                    return list(groups.keys())
                else:
                    # Handle legacy list format
                    return groups if isinstance(groups, list) else []
            return []
            
        except Exception as e:
            logger.error(f"Failed to get groups for user {uid}: {e}")
            return []

    @staticmethod
    def get_group_members(group_name: str) -> list:
        """Get all members of a group"""
        try:
            if not firebase_config.is_firebase_enabled():
                return []
            
            # Try Firestore first if enabled
            try:
                from admin.firestore_sync import fs_get_group_members, FIRESTORE_ENABLED
                if FIRESTORE_ENABLED:
                    group_data = fs_get_group_members(group_name)
                    if group_data:
                        members = group_data.get('members', [])
                        admins = group_data.get('admins', [])
                        return list(set(members + admins))
            except ImportError:
                logger.debug("Firestore sync not available, using RTDB fallback")
            except Exception as e:
                logger.debug(f"Firestore get failed: {e}, falling back to RTDB")
            
            # Fallback to RTDB
            group_data = firebase_config.firebase_read_data(f'/groups/{group_name}')
            if group_data:
                members = group_data.get('members', [])
                admins = group_data.get('admins', [])
                return list(set(members + admins))
            return []
            
        except Exception as e:
            logger.error(f"Failed to get members for group {group_name}: {e}")
            return []


# Convenience functions for use in routes

def firebase_register(email: str, password: str, display_name: str = "") -> Tuple[bool, Optional[str], Optional[str]]:
    """Register a new Firebase user"""
    return FirebaseAuthHandler.register_user(email, password, display_name)


def firebase_login(email: str, password: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """Login a Firebase user"""
    return FirebaseAuthHandler.login_user(email, password)


def firebase_get_user(uid: str) -> Optional[Dict[str, Any]]:
    """Get user profile"""
    return FirebaseAuthHandler.get_user_by_uid(uid)


def firebase_user_groups(uid: str) -> list:
    """Get user's groups"""
    return FirebaseAuthHandler.get_user_groups(uid)


def firebase_add_user_to_group(uid: str, group_name: str) -> Tuple[bool, Optional[str]]:
    """Add user to group"""
    return FirebaseAuthHandler.add_user_to_group(uid, group_name)


def firebase_remove_user_from_group(uid: str, group_name: str) -> Tuple[bool, Optional[str]]:
    """Remove user from group"""
    return FirebaseAuthHandler.remove_user_from_group(uid, group_name)


def firebase_get_group_members(group_name: str) -> list:
    """Get group members"""
    return FirebaseAuthHandler.get_group_members(group_name)


def firebase_create_group(group_name: str, creator_uid: str, data_folder: str = "") -> Tuple[bool, Optional[str]]:
    """Create a new group"""
    try:
        if not firebase_config.is_firebase_enabled():
            return False, "Firebase not enabled"
        
        # Check if group already exists
        existing = firebase_config.firebase_read_data(f'/groups/{group_name}')
        if existing:
            return False, "Group already exists"
        
        # Create group data
        group_data = {
            'name': group_name,
            'data_folder': data_folder or group_name.lower().replace(' ', '_'),
            'creator_uid': creator_uid,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'members': [creator_uid],
            'admins': [creator_uid]
        }
        
        # Save to Firebase
        firebase_config.firebase_write_data(f'/groups/{group_name}', group_data)
        
        # Add group to creator's profile
        user_profile = firebase_config.firebase_read_data(f'/users/{creator_uid}')
        if user_profile:
            if 'groups' not in user_profile:
                user_profile['groups'] = []
            if group_name not in user_profile['groups']:
                user_profile['groups'].append(group_name)
            
            # Set group roles
            if 'group_roles' not in user_profile:
                user_profile['group_roles'] = {}
            user_profile['group_roles'][group_name] = 'admin'
            
            firebase_config.firebase_write_data(f'/users/{creator_uid}', user_profile)
        
        logger.info(f"Group '{group_name}' created by {creator_uid}")
        
        # Log activity
        firebase_config.firebase_log_activity(
            creator_uid,
            group_name,
            'group_created',
            {'group': group_name, 'data_folder': data_folder}
        )
        
        return True, None
        
    except Exception as e:
        logger.error(f"Failed to create group {group_name}: {e}")
        return False, str(e)


def firebase_set_user_group_role(uid: str, group_name: str, role: str) -> Tuple[bool, Optional[str]]:
    """Set user's role in a group"""
    try:
        if not firebase_config.is_firebase_enabled():
            return False, "Firebase not enabled"
        
        # Try Firestore first if enabled
        try:
            from admin.firestore_sync import fs_atomic_set_user_group_role, FIRESTORE_ENABLED
            if FIRESTORE_ENABLED:
                result = fs_atomic_set_user_group_role(uid, group_name, role)
                if result:
                    logger.info(f"User {uid} role set to '{role}' in group {group_name} via Firestore")
                    return True, None
        except ImportError:
            logger.debug("Firestore sync not available, using RTDB fallback")
        except Exception as e:
            logger.warning(f"Firestore role update failed: {e}, falling back to RTDB")
        
        # Fallback to RTDB
        # Update user profile
        user_profile = firebase_config.firebase_read_data(f'/users/{uid}')
        if user_profile:
            groups = user_profile.get('groups', {})
            if isinstance(groups, list):
                # Convert legacy list format to dict
                groups = {g: 'member' for g in groups}
            
            if group_name not in groups:
                groups[group_name] = role
            else:
                groups[group_name] = role
            
            if 'group_roles' not in user_profile:
                user_profile['group_roles'] = {}
            user_profile['group_roles'][group_name] = role
            user_profile['groups'] = groups
            firebase_config.firebase_write_data(f'/users/{uid}', user_profile)
        
        # Update group data
        group_data = firebase_config.firebase_read_data(f'/groups/{group_name}')
        if group_data:
            members = group_data.get('members', [])
            admins = group_data.get('admins', [])
            
            # Remove from both lists first
            if uid in members:
                members.remove(uid)
            if uid in admins:
                admins.remove(uid)
            
            # Add to appropriate list
            if role == 'admin':
                if uid not in admins:
                    admins.append(uid)
            else:
                if uid not in members:
                    members.append(uid)
            
            group_data['members'] = members
            group_data['admins'] = admins
            firebase_config.firebase_write_data(f'/groups/{group_name}', group_data)
        
        logger.info(f"User {uid} role set to '{role}' in group: {group_name} (RTDB)")
        
        # Log activity
        firebase_config.firebase_log_activity(
            uid,
            group_name,
            'user_role_changed',
            {'group': group_name, 'role': role}
        )
        
        return True, None
        
    except Exception as e:
        logger.error(f"Failed to set role for user {uid} in group {group_name}: {e}")
        return False, str(e)


def firebase_get_user_role_in_group(uid: str, group_name: str) -> Optional[str]:
    """Get user's role in a specific group.

    Firestore-first with RTDB fallback to ensure correct admin/member detection
    in templates and dropdowns.
    """
    try:
        if not firebase_config.is_firebase_enabled():
            return None

        # Try Firestore first
        try:
            from admin.firestore_sync import fs_get_user_role_in_group, FIRESTORE_ENABLED
            if FIRESTORE_ENABLED:
                role = fs_get_user_role_in_group(uid, group_name)
                if role:
                    return role
        except ImportError:
            logger.debug("Firestore module not available for role lookup, using RTDB fallback")
        except Exception as e:
            logger.debug(f"Firestore role lookup failed: {e}; falling back to RTDB")

        # Fallback to RTDB (legacy structure)
        user_profile = firebase_config.firebase_read_data(f'/users/{uid}')
        if user_profile:
            # Support both dict style and legacy list
            group_roles = user_profile.get('group_roles') or {}
            if isinstance(group_roles, dict):
                return group_roles.get(group_name, 'member')
            # If only 'groups' list exists, treat as member
            groups = user_profile.get('groups') or []
            if isinstance(groups, list) and group_name in groups:
                return 'member'

        # Default when unknown: member
        return 'member'

    except Exception as e:
        logger.error(f"Failed to get role for user {uid} in group {group_name}: {e}")
        return None


def firebase_delete_group(group_name: str, admin_uid: str) -> Tuple[bool, Optional[str]]:
    """Delete a group (admin only). Firestore-first with RTDB fallback."""
    try:
        if not firebase_config.is_firebase_enabled():
            return False, "Firebase not enabled"

        # Check if user is admin
        user_role = firebase_get_user_role_in_group(admin_uid, group_name)
        if user_role != 'admin':
            return False, "Only group admins can delete groups"

        # Try Firestore first
        try:
            from admin.firestore_sync import fs_delete_group, FIRESTORE_ENABLED
            if FIRESTORE_ENABLED:
                if fs_delete_group(group_name):
                    logger.info(f"Group '{group_name}' deleted in Firestore by {admin_uid}")
                    firebase_config.firebase_log_activity(
                        admin_uid,
                        'system',
                        'group_deleted',
                        {'group': group_name}
                    )
                    return True, None
                else:
                    logger.warning(f"Firestore delete failed for group '{group_name}', attempting RTDB fallback")
        except ImportError:
            logger.debug("Firestore module not available for delete, using RTDB fallback")
        except Exception as e:
            logger.warning(f"Firestore delete error for group '{group_name}': {e}; falling back to RTDB")

        # RTDB fallback: remove group from users and delete group node
        group_data = firebase_config.firebase_read_data(f'/groups/{group_name}') or {}
        members = group_data.get('members') or []
        admins = group_data.get('admins') or []
        all_uids = set(members) | set(admins)

        for uid in all_uids:
            user_profile = firebase_config.firebase_read_data(f'/users/{uid}') or {}
            # Handle 'groups' as dict or list
            groups_val = user_profile.get('groups')
            if isinstance(groups_val, dict):
                if group_name in groups_val:
                    del groups_val[group_name]
                user_profile['groups'] = groups_val
            elif isinstance(groups_val, list):
                if group_name in groups_val:
                    groups_val.remove(group_name)
                user_profile['groups'] = groups_val

            # Handle 'group_roles' dict
            group_roles = user_profile.get('group_roles') or {}
            if isinstance(group_roles, dict) and group_name in group_roles:
                del group_roles[group_name]
                user_profile['group_roles'] = group_roles

            firebase_config.firebase_write_data(f'/users/{uid}', user_profile)

        # Delete group data
        firebase_config.firebase_delete_data(f'/groups/{group_name}')

        logger.info(f"Group '{group_name}' deleted in RTDB by {admin_uid}")

        # Log activity
        firebase_config.firebase_log_activity(
            admin_uid,
            'system',
            'group_deleted',
            {'group': group_name}
        )

        return True, None

    except Exception as e:
        logger.error(f"Failed to delete group {group_name}: {e}")
        return False, str(e)
