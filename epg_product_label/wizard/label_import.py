# -*- coding: utf-8 -*-
import base64

from odoo import _, fields, models
from odoo.exceptions import UserError


class EpgLabelImport(models.TransientModel):
    _name = 'epg.label.import'
    _description = 'Import Label Templates'

    file_data = fields.Binary(string='XML File', required=True)
    filename = fields.Char()
    overwrite = fields.Boolean(
        string='Overwrite Existing', default=False,
        help="Replace templates that already carry the same reference. Their "
             "elements are deleted and rebuilt from the file. Leave unchecked to "
             "import them alongside the existing ones.")

    def action_import(self):
        self.ensure_one()
        try:
            payload = base64.b64decode(self.file_data)
        except Exception as error:
            raise UserError(_("The uploaded file could not be read.")) from error
        templates = self.env['epg.label.template']._import_from_xml(
            payload, overwrite=self.overwrite)
        if not templates:
            raise UserError(_("The file did not contain any label template."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Imported Templates'),
            'res_model': 'epg.label.template',
            'view_mode': 'list,form' if len(templates) > 1 else 'form',
            'res_id': templates.id if len(templates) == 1 else False,
            'domain': [('id', 'in', templates.ids)],
        }
