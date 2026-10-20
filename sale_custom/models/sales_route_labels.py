# -*- coding: utf-8 -*-
"""The lab calls a sales team a Sales Route (client, 2026-10-20: "Change label Sales
Team to Sales Route in all places including filters, menus, labels").

Only what people read changes - field and model names stay, so nothing that reads
them moves. An order's and a contact's own team_id were renamed where they are
defined (sale_order.py, res_partner.py); these are the rest, found by listing every
field label and tooltip in the database that said "Sales Team". Views, menus and
actions are in views/sales_route_labels_views.xml.
"""
from odoo import fields, models


class CrmTeam(models.Model):
    _inherit = 'crm.team'
    _description = 'Sales Route'

    name = fields.Char(string='Sales Route')
    crm_team_member_ids = fields.One2many(
        string='Sales Route Members',
        help="Add members to automatically assign their documents to this sales route.")
    crm_team_member_all_ids = fields.One2many(string='Sales Route Members (incl. inactive)')
    is_membership_multi = fields.Boolean(
        help="If True, users may belong to several sales routes. Otherwise membership is "
             "limited to a single sales route.")
    active = fields.Boolean(
        help="If the active field is set to false, it will allow you to hide the Sales "
             "Route without removing it.")


class CrmTeamMember(models.Model):
    _inherit = 'crm.team.member'
    _description = 'Sales Route Member'

    crm_team_id = fields.Many2one(string='Sales Route')
    is_membership_multi = fields.Boolean(
        help="If True, users may belong to several sales routes. Otherwise membership is "
             "limited to a single sales route.")


class ResUsers(models.Model):
    _inherit = 'res.users'

    crm_team_ids = fields.Many2many(string='Sales Routes')
    crm_team_member_ids = fields.One2many(string='Sales Route Members')
    sale_team_id = fields.Many2one(
        string='User Sales Route',
        help="Main user sales route. Used notably for pipeline, or to set the sales route "
             "in invoicing or subscription.")


class AccountMove(models.Model):
    _inherit = 'account.move'

    team_id = fields.Many2one(string='Sales Route')


# account.bank.statement.line takes team_id from its journal entry (_inherits
# account.move), label included: redeclaring it there would make a field of its own.


class AccountInvoiceReport(models.Model):
    _inherit = 'account.invoice.report'

    team_id = fields.Many2one(string='Sales Route')


class SaleReport(models.Model):
    _inherit = 'sale.report'

    team_id = fields.Many2one(string='Sales Route')


class SaleOrderTemplate(models.Model):
    _inherit = 'sale.order.template'

    team_ids = fields.Many2many(string='Sales Routes')
