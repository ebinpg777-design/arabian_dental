# -*- coding: utf-8 -*-
from . import models
from . import wizard


def _uninstall_hook(env):
    """Give the app menu back the name the account module uses."""
    menu = env.ref('account.menu_finance', raise_if_not_found=False)
    if menu:
        menu.name = 'Invoicing'
