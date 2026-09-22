# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Handed to write() by SERVER code that performs a guarded reset (see
# ``_lock_reset_write``). It is compared with ``is``: an RPC context is decoded from
# JSON, and JSON cannot produce this object, so a client cannot claim to be a reset
# by putting a key in its context.
LOCK_RESET = object()
LOCK_RESET_KEY = 'lab_lock_reset'


class LabLockMixin(models.AbstractModel):
    """Make a record genuinely read-only once it reaches a closed state.

    A ``readonly=`` in the form view is a *hint*: it stops the web client rendering an
    editable widget, and nothing else. A user who can reach the record can still change
    it from a list view, an import, a server action or plain RPC. For records that carry
    money or attendance evidence (a cleared cheque, an approved travel log, a completed
    visit) that gap matters, so the rule is enforced in ``write`` where it cannot be
    stepped around.

    Models opt in by setting:

    ``_lock_states``
        States in which the record is locked. Empty -> mixin does nothing.
    ``_lock_exempt_fields``
        Business fields that stay writable while locked (e.g. a remark the accountant
        must still be able to add after clearing).
    ``_lock_bypass_groups``
        XML ids of groups allowed to edit anyway (the supervising role).

    The state field itself is always writable: workflow buttons have to be able to move
    the record on (and back off) a locked state.

    A write that moves a record OUT of a locked state (a reset) may also CLEAR fields —
    set them to False / 0 / empty — because a reset exists to wipe the stamps the closed
    state left behind (deposit and clear dates on a bounced cheque, check-in times on a
    reopened visit). It may not SET them: ``write({'state': 'draft', 'odo_end': 9999})``
    on an approved trip is an edit of closed evidence wearing a reset's clothes, and is
    refused. A reset that genuinely has to set values goes through
    ``_lock_reset_write``, which only server code can call.

    Trade-off, stated plainly: the lock protects a record while it is closed. Whoever
    may reset a record may edit it once it is open again, in a second write — the
    control on that is who may reset (the model's own buttons and groups) and the
    state change it leaves in the chatter, not this mixin.
    """
    _name = 'lab.lock.mixin'
    _description = 'Lab State-based Write Lock'

    _lock_states = ()
    _lock_exempt_fields = ()
    _lock_bypass_groups = ()
    _lock_state_field = 'state'

    # Never guard the framework's own plumbing. Blocking these would break the chatter,
    # activities and the ORM's internal bookkeeping rather than protect anything.
    _LOCK_ALWAYS_ALLOWED = {
        'state', 'write_date', 'write_uid', 'create_date', 'create_uid',
        'message_ids', 'message_follower_ids', 'message_partner_ids',
        'message_main_attachment_id', 'activity_ids', 'activity_state',
        'activity_user_id', 'activity_type_id', 'activity_date_deadline',
        'activity_summary', 'activity_exception_decoration', 'activity_exception_icon',
        'website_message_ids', 'message_has_error', 'message_needaction',
        'message_attachment_count', 'access_token', 'display_name',
    }

    # Exposed so a form can explain the lock instead of letting the user type into a
    # record that will refuse to save. Computed from _lock_states, so a model that
    # changes its locked states gets the banner and the read-only fields for free —
    # the view can never drift from what write() actually enforces.
    lock_is_locked = fields.Boolean(
        string='Locked', compute='_compute_lock_state', help="Closed record: read-only.")
    lock_can_edit = fields.Boolean(
        string='May Edit Locked', compute='_compute_lock_state',
        help="The current user holds the supervising role and can still edit this record.")
    lock_message = fields.Char(compute='_compute_lock_state')

    @api.depends(lambda self: (self._lock_state_field,))
    def _compute_lock_state(self):
        bypassed = self._lock_is_bypassed() if self._lock_states else False
        field = self._lock_state_field
        labels = dict(self._fields[field]._description_selection(self.env)) \
            if field in self._fields else {}
        for record in self:
            locked = bool(self._lock_states) and record[field] in self._lock_states
            record.lock_is_locked = locked
            record.lock_can_edit = bypassed
            if not locked:
                record.lock_message = False
            elif bypassed:
                record.lock_message = _(
                    "This record is %(state)s. It is read-only for everyone except your "
                    "role — edit it only if you mean to change a closed record.",
                    state=labels.get(record[field], record[field]))
            else:
                record.lock_message = _(
                    "This record is %(state)s and can no longer be edited. Reset it to an "
                    "open state, or ask a user with the supervising role to make the change.",
                    state=labels.get(record[field], record[field]))

    def _lock_is_bypassed(self):
        """True when the current user may edit a locked record anyway."""
        if self.env.su or self.env.user._is_superuser() or self.env.user._is_admin():
            return True
        return any(self.env.user.has_group(g) for g in self._lock_bypass_groups)

    def _lock_exempt_fields_for(self, state):
        """Fields that stay writable in ONE locked state.

        Closed does not always mean the same thing. A completed visit still has
        to be able to say what happened at the counter; a cancelled one must
        not, because nothing happened. Models that need the distinction
        override this - by default every locked state exempts the same fields.
        (client, 2026-09-12)
        """
        return self._lock_exempt_fields

    def _lock_guarded_fields(self, vals, state=None, leaving=False):
        """Business fields in ``vals`` that a locked record must not accept.

        Computed / related / non-stored fields are skipped: those are written by the ORM
        itself during recomputation, not by the user, and guarding them would raise on
        perfectly legitimate internal flushes.

        ``leaving``: the same write moves the record out of its locked state. Fields
        being cleared then pass — that is what a reset does — and fields being set to
        a value do not.
        """
        allowed = set(self._LOCK_ALWAYS_ALLOWED)
        allowed.add(self._lock_state_field)
        allowed.update(self._lock_exempt_fields_for(state) if state
                       else self._lock_exempt_fields)
        guarded = []
        for name in vals:
            if name in allowed:
                continue
            field = self._fields.get(name)
            if field is None or not field.store:
                continue
            # Stored computed fields without an inverse are ORM-managed.
            if field.compute and not field.inverse:
                continue
            if field.related:
                continue
            if leaving and not vals[name]:
                continue
            guarded.append(name)
        return guarded

    def _lock_locked_records(self):
        states = set(self._lock_states)
        if not states:
            return self.browse()
        field = self._lock_state_field
        return self.filtered(lambda r: r[field] in states)

    def _lock_check_write(self, vals):
        """Raise if ``vals`` would change a closed record. Called by ``write``."""
        if not self._lock_states or self._lock_is_bypassed():
            return
        field = self._lock_state_field
        new_state = vals.get(field)
        leaving = new_state is not None and new_state not in self._lock_states
        if leaving and self.env.context.get(LOCK_RESET_KEY) is LOCK_RESET:
            # A reset performed by the model's own server code: it knows what the
            # reopened record has to look like.
            return
        # Record by record: what a locked state allows can differ between
        # them, so one write of two visits - one done, one cancelled - is
        # judged by each visit's own state. (client, 2026-09-12)
        for record in self._lock_locked_records():
            state = record[field]
            guarded = self._lock_guarded_fields(vals, state, leaving=leaving)
            if not guarded:
                continue
            blocked = ', '.join(
                self._fields[f].string or f for f in guarded if f in self._fields)
            labels = dict(self._fields[field]._description_selection(self.env))
            if leaving:
                raise UserError(_(
                    "%(record)s is %(state)s. Resetting it may clear what was recorded "
                    "when it closed, but not change it in the same step.\n\n"
                    "Blocked field(s): %(fields)s\n\n"
                    "Reset the record first, then make the change, or ask a user with "
                    "the supervising role.",
                    record=record.display_name or self._description,
                    state=labels.get(state, state), fields=blocked,
                ))
            raise UserError(_(
                "%(record)s is %(state)s and can no longer be edited.\n\n"
                "Blocked field(s): %(fields)s\n\n"
                "Reset the record to an open state, or ask a user with the "
                "supervising role to make the change.",
                record=record.display_name or self._description,
                state=labels.get(state, state), fields=blocked,
            ))

    def write(self, vals):
        self._lock_check_write(vals)
        return super().write(vals)

    def _lock_reset_write(self, vals):
        """Reset a closed record, setting values the reopened record needs.

        For a model's own reset method. A plain ``write`` that leaves a locked state
        may only clear fields; this may set them too. Private (underscore), so it
        cannot be called over RPC.
        """
        return self.with_context(**{LOCK_RESET_KEY: LOCK_RESET}).write(vals)

    @api.model
    def _lock_state_domain_open(self):
        """Domain helper for views/actions that should only offer open records."""
        return [(self._lock_state_field, 'not in', list(self._lock_states))]
