"""Admin pages package."""
from .dashboard import admin_dashboard_page
from .group_detail import admin_group_detail_page
from .groups import admin_groups_page
from .settings import admin_settings_page
from .user_detail import admin_user_detail_page
from .users import admin_users_page

__all__ = [
    "admin_dashboard_page",
    "admin_users_page",
    "admin_user_detail_page",
    "admin_groups_page",
    "admin_group_detail_page",
    "admin_settings_page",
]
