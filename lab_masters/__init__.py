from . import models
# Uninstalling must not take the lab's own records with it; see the hook.
from .models.masters_loader import uninstall_hook
