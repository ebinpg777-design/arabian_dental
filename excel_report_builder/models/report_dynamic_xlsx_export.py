# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebin P G
# License OPL-1. See LICENSE file for full copyright and licensing details.
from odoo import models, api, http, fields as odoo_fields
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.http import request
from odoo.tools.safe_eval import safe_eval, datetime as safe_datetime, time as safe_time
from dateutil.relativedelta import relativedelta
from markupsafe import escape
import ast
import functools
import io
import csv
import json
import logging
import datetime
import operator
import re

_logger = logging.getLogger(__name__)

try:
    import xlsxwriter
    XLSXWRITER_AVAILABLE = True
except ImportError:
    xlsxwriter = None
    XLSXWRITER_AVAILABLE = False


class DynamicExportWizard(models.AbstractModel):
    _name = 'report.dynamic_xlsx_export'
    _description = 'Dynamic XLSX Export Engine'

    @api.model
    def get_model_fields(self, model_name):
        model = self.env.get(model_name)
        if model is None:
            return []
        allowed_types = {
            'char', 'text', 'integer', 'float', 'monetary', 'boolean',
            'date', 'datetime', 'selection', 'many2one', 'many2many', 'one2many',
        }
        relational = {'many2one', 'many2many', 'one2many'}
        result = []
        for name, info in model.fields_get().items():
            if info['type'] in allowed_types:
                result.append({
                    'name': name,
                    'string': info['string'],
                    'type': info['type'],
                    'relation_model': info.get('relation') if info['type'] in relational else False,
                    'store': info.get('store', False),
                })
        return sorted(result, key=lambda x: x['string'].lower())


# ── Shared helpers ─────────────────────────────────────────────────────────────

_NUMERIC_TYPES = {'integer', 'float', 'monetary', 'computed'}


# ── Computed-column formulas ───────────────────────────────────────────────────
#
# A formula comes straight from the request (or a saved template), so it is never
# handed to eval(). It is parsed with ast and walked against a whitelist: numbers,
# the row's {column} values, arithmetic, comparisons and a few math functions.
# Anything else - attribute access, subscripts, strings, lambdas - is refused.

class FormulaError(ValueError):
    """The formula uses something outside the computed-column whitelist."""


_FORMULA_FUNCTIONS = {
    'round': round, 'abs': abs, 'min': min, 'max': max,
    'int': int, 'float': float, 'pow': pow,
}
_FORMULA_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_FORMULA_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}
_FORMULA_COMPARE = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}
_FORMULA_MAX_LENGTH = 2000
_FORMULA_MAX_EXPONENT = 100   # 10 ** 10 ** 10 would pin a worker for ever
_FORMULA_TOKEN = re.compile(r'\{([^}]+)\}')


@functools.lru_cache(maxsize=512)
def _parse_formula(formula):
    """Return (expression node, column tokens) for *formula*.

    Each ``{column}`` token becomes a plain variable ``_col<N>``, so a column
    name - which may hold slashes - never reaches the parser as code.
    """
    if len(formula) > _FORMULA_MAX_LENGTH:
        raise FormulaError("formula is too long")
    tokens = []

    def _placeholder(match):
        tokens.append(match.group(1).strip())
        return ' _col%d ' % (len(tokens) - 1)

    expr = _FORMULA_TOKEN.sub(_placeholder, formula).strip()
    try:
        tree = ast.parse(expr, mode='eval')
    except SyntaxError as err:
        raise FormulaError("invalid formula: %s" % err.msg) from err
    return tree.body, tuple(tokens)


def _check_exponent(exponent):
    if isinstance(exponent, (int, float)) and abs(exponent) > _FORMULA_MAX_EXPONENT:
        raise FormulaError("exponent is too large")


def _eval_formula_node(node, values):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, complex):
            return node.value
        raise FormulaError("only numbers are allowed")
    if isinstance(node, ast.Name):
        if node.id in values:
            return values[node.id]
        raise FormulaError("unknown name %r" % node.id)
    if isinstance(node, ast.BinOp) and type(node.op) in _FORMULA_BINARY:
        left = _eval_formula_node(node.left, values)
        right = _eval_formula_node(node.right, values)
        if isinstance(node.op, ast.Pow):
            _check_exponent(right)
        return _FORMULA_BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _FORMULA_UNARY:
        return _FORMULA_UNARY[type(node.op)](_eval_formula_node(node.operand, values))
    if isinstance(node, ast.Compare) and all(type(op) in _FORMULA_COMPARE for op in node.ops):
        left = _eval_formula_node(node.left, values)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_formula_node(comparator, values)
            if not _FORMULA_COMPARE[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        result = None
        for operand in node.values:
            result = _eval_formula_node(operand, values)
            if isinstance(node.op, ast.And) and not result:
                return result
            if isinstance(node.op, ast.Or) and result:
                return result
        return result
    if isinstance(node, ast.IfExp):
        branch = node.body if _eval_formula_node(node.test, values) else node.orelse
        return _eval_formula_node(branch, values)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in _FORMULA_FUNCTIONS and not node.keywords):
        args = [_eval_formula_node(arg, values) for arg in node.args]
        if node.func.id == 'pow' and len(args) >= 2:
            _check_exponent(args[1])
        return _FORMULA_FUNCTIONS[node.func.id](*args)
    raise FormulaError("%s is not allowed in a formula" % type(node).__name__)


def evaluate_formula(formula, resolve):
    """Evaluate *formula*; ``resolve(column)`` returns a column's number.

    Raises :class:`FormulaError` when the formula leaves the whitelist.
    """
    node, tokens = _parse_formula(formula)
    values = {'_col%d' % index: resolve(token) for index, token in enumerate(tokens)}
    return _eval_formula_node(node, values)


def _compute_formula_value(formula, row_data, record=None):
    """Evaluate a formula like '{price_unit} * {product_uom_qty}' against row data.

    Tokens {field_name} resolve to numbers. Resolution order for each token: the
    already-collected ``row_data`` (selected columns) first, then, when a
    ``record`` is supplied, the field is read directly from that record. This
    lets a formula reference any field of the model — not only the columns the
    user chose to export. See :func:`evaluate_formula` for what a formula may use.
    """
    if not formula:
        return ''

    def _resolve_token(token):
        if token in row_data:
            return _token_to_number(row_data[token])
        if record is not None:
            return _token_to_number(_extract_field_value(record, {'name': token}))
        return 0

    try:
        result = evaluate_formula(formula, _resolve_token)
        if isinstance(result, bool):
            return 1 if result else 0
        if isinstance(result, (int, float)):
            return result
        return ''
    except ZeroDivisionError:
        return ''
    except Exception as e:
        _logger.warning("Formula '%s' failed: %s", formula, e)
        return ''


def _token_to_number(val):
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, (int, float)):
        return val
    try:
        return float(str(val).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0


# ── Export domains ─────────────────────────────────────────────────────────────

def _domain_eval_context(env):
    """Names a stored / posted domain may use - the same ones the web client offers."""
    today = odoo_fields.Date.context_today(env.user)
    return {
        'uid': env.uid,
        'user': env.user,
        'context': dict(env.context),
        'datetime': safe_datetime,
        'time': safe_time,
        'relativedelta': relativedelta,
        'context_today': lambda: odoo_fields.Date.context_today(env.user),
        'today': odoo_fields.Date.to_string(today),
        'current_date': odoo_fields.Date.to_string(today),
        'now': odoo_fields.Datetime.to_string(odoo_fields.Datetime.now()),
        'company_id': env.company.id,
        'company_ids': env.companies.ids,
        'allowed_company_ids': env.companies.ids,
    }


def parse_domain(env, raw):
    """Turn a domain received as text into a domain list.

    Accepts both JSON (``[["active", "=", true]]``, what older clients sent and
    older templates stored) and a Python literal (``[('active', '=', True)]``,
    what the domain editor produces). A domain that cannot be read raises a
    UserError: exporting the whole model instead would be silently wrong.
    """
    if isinstance(raw, (list, tuple)):
        value = list(raw)
    else:
        text = (raw or '').strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except ValueError:
            try:
                value = safe_eval(text, _domain_eval_context(env))
            except Exception as err:
                raise UserError(env._("The report filter could not be read: %s", err)) from err
    if not isinstance(value, (list, tuple)):
        raise UserError(env._("The report filter is not a domain: %s", raw))
    try:
        Domain(list(value))
    except Exception as err:
        raise UserError(env._("The report filter is not a valid domain: %s", err)) from err
    return list(value)


def _sanitize_filename(raw, fallback):
    if not raw or not raw.strip():
        return fallback
    safe = re.sub(r'[^\w\s\-]', '', raw.strip())
    return safe.strip() or fallback


def _safe_sheet_name(name, fallback='Data'):
    safe = re.sub(r'[\\/*?\[\]:]', '', (name or fallback).strip()) or fallback
    return safe[:31]  # Excel sheet name max 31 chars


def _is_multi_recordset(val):
    """True when val is an Odoo recordset with more than one record."""
    return (
        hasattr(val, '_name')
        and hasattr(val, '__iter__')
        and not isinstance(val, str)
        and len(val) > 1
    )


def _extract_field_value(rec, field):
    """Traverse a possibly nested field path and return a plain Python value.

    Handles one2many / many2many by using .mapped() when a multi-record set
    is encountered mid-path, preventing 'Expected singleton' errors.
    """
    path_parts = field['name'].split('/')
    value = rec
    try:
        for part in path_parts:
            if not value:
                return ''
            if _is_multi_recordset(value):
                # Traverse across all records in the set; result may be a
                # recordset (relational) or a list of scalars.
                value = value.mapped(part)
            else:
                value = value[part]
    except (KeyError, AttributeError, TypeError, ValueError):
        return ''

    if value is False or value is None:
        return ''

    # Recordset (o2m, m2m, or mapped result)
    if hasattr(value, '_name') and hasattr(value, '__iter__') and not isinstance(value, str):
        return ', '.join(r.display_name for r in value if r.display_name)

    # Plain list/tuple from mapped() on scalar fields (e.g. mapped('price_unit'))
    if isinstance(value, (list, tuple)):
        return ', '.join(str(v) for v in value if v not in (False, None, ''))

    # Single many2one record
    if hasattr(value, 'display_name'):
        return value.display_name

    if isinstance(value, bytes):
        try:
            return value.decode('utf-8')
        except Exception:
            return ''

    return value


def _group_label(g, key, base_field):
    """Resolve a read_group bucket's dimension label for *key* (may be 'field:period')."""
    raw = g.get(key, g.get(base_field))
    if isinstance(raw, tuple):
        return raw[1] if len(raw) > 1 else raw[0]
    if raw in (False, None):
        return "Undefined / Empty"
    return raw


def _collect_export_data(env, model_name, field_list, field_type, groupby,
                         final_domain, sort_field='', sort_dir='asc', export_limit=0):
    """Collect export rows as a list of dicts, shared by XLSX and CSV."""
    rows = []
    computed_fields = [
        f for f in field_list
        if f.get('type') == 'computed' and f.get('formula')
    ]

    if field_type == 'group' and groupby:
        # Support comma-separated multi-groupby: "field1,field2:month,field3"
        groupby_list = [g.strip() for g in groupby.split(',') if g.strip()]
        base_fields = {g.split(':')[0] for g in groupby_list}

        # Computed columns are derived after aggregation — never send them to
        # read_group as measures or it raises on the non-existent field.
        measure_fields = [
            f for f in field_list
            if f['name'] not in base_fields and f['name'] != '__count'
            and f.get('type') != 'computed'
        ]
        agg_fields = [
            f"{f['name']}:{f.get('agg_type') or 'sum'}"
            for f in measure_fields
        ]
        try:
            groups = env[model_name].read_group(
                domain=final_domain,
                fields=agg_fields,
                groupby=groupby_list,
                lazy=False,
            )
        except Exception as e:
            _logger.error("read_group failed for %s: %s", model_name, e)
            groups = []
        for g in groups:
            row = {}
            # All group-by dimension columns
            for gb_spec in groupby_list:
                base = gb_spec.split(':')[0]
                row[base] = _group_label(g, gb_spec, base)
            # Record count (always available from read_group)
            row['__count'] = g.get('__count', 0)
            # Measure aggregations
            for f in measure_fields:
                agg_fn = f.get('agg_type') or 'sum'
                key = f"{f['name']}:{agg_fn}"
                row[f['name']] = g.get(key, g.get(f['name'], 0))
            rows.append(row)
    else:
        order = f"{sort_field} {sort_dir}" if sort_field else None
        limit = int(export_limit) if export_limit else 0
        try:
            records = env[model_name].search(final_domain, order=order, limit=limit)
        except Exception as e:
            _logger.error("search failed for %s: %s", model_name, e)
            records = env[model_name].browse()
        for rec in records:
            row = {f['name']: _extract_field_value(rec, f) for f in field_list}
            # Resolve formulas against the live record so any field can be
            # referenced, not just the columns selected for export.
            for f in computed_fields:
                row[f['name']] = _compute_formula_value(f['formula'], row, rec)
            rows.append(row)
        return _apply_analytic_transforms(field_list, rows)

    # Grouped mode: formulas operate on the aggregated measure values only.
    for f in computed_fields:
        for row in rows:
            row[f['name']] = _compute_formula_value(f['formula'], row)

    return _apply_analytic_transforms(field_list, rows)


# ── Analytic column transforms ──────────────────────────────────────────────

_TRANSFORMS = {'running_total', 'pct_of_total', 'rank'}


def _column_numeric_values(rows, name):
    """Yield (index, numeric_value) for rows where the column holds a number."""
    out = []
    for i, r in enumerate(rows):
        v = r.get(name)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append((i, v))
    return out


def _apply_analytic_transforms(field_list, rows):
    """Rewrite a column's values in place according to its ``transform``.

    Supported transforms (numeric columns only):
      * running_total — cumulative sum down the rows
      * pct_of_total  — each value as a percentage of the column total
      * rank          — descending dense rank (1 = largest)
    Columns carrying a transform are flagged so the totals row can skip them.
    """
    for f in field_list:
        transform = f.get('transform')
        if transform not in _TRANSFORMS:
            continue
        name = f['name']
        numeric = _column_numeric_values(rows, name)
        if not numeric:
            continue
        if transform == 'running_total':
            cumulative = 0
            for idx, val in numeric:
                cumulative += val
                rows[idx][name] = cumulative
        elif transform == 'pct_of_total':
            total = sum(v for _, v in numeric)
            for idx, val in numeric:
                rows[idx][name] = round((val / total) * 100, 2) if total else 0
        elif transform == 'rank':
            order = sorted(numeric, key=lambda iv: iv[1], reverse=True)
            rank_by_idx = {idx: pos + 1 for pos, (idx, _) in enumerate(order)}
            for idx, _ in numeric:
                rows[idx][name] = rank_by_idx[idx]
    return rows


# ── Conditional formatting ──────────────────────────────────────────────────

def _apply_conditional_format(workbook, ws, f, first_row, last_row, col):
    """Apply a per-column conditional-format rule to a data range.

    Supports legacy ``heatmap`` boolean plus the richer ``cond_format`` modes:
    heatmap (3-colour), 2_color, data_bar, icons and a custom threshold rule.
    """
    mode = f.get('cond_format') or ('heatmap' if f.get('heatmap') else '')
    if not mode or last_row < first_row:
        return
    rng = (first_row, col, last_row, col)
    if mode in ('heatmap', '3_color'):
        ws.conditional_format(*rng, {
            'type': '3_color_scale',
            'min_color': '#FF6B6B', 'mid_color': '#FFD93D', 'max_color': '#6BCF7F',
        })
    elif mode == '2_color':
        ws.conditional_format(*rng, {
            'type': '2_color_scale', 'min_color': '#FFFFFF', 'max_color': '#1a7340',
        })
    elif mode == 'data_bar':
        ws.conditional_format(*rng, {'type': 'data_bar', 'bar_color': '#4F86C6'})
    elif mode == 'icons':
        ws.conditional_format(*rng, {'type': 'icon_set', 'icon_style': '3_traffic_lights'})
    elif mode == 'threshold':
        op = f.get('cond_op') or '>'
        if op not in ('>', '>=', '<', '<=', '==', '!='):
            op = '>'
        try:
            value = float(f.get('cond_value'))
        except (TypeError, ValueError):
            value = 0.0
        color = f.get('cond_color') or '#FFC7CE'
        cell_fmt = workbook.add_format({'bg_color': color, 'border': 1})
        ws.conditional_format(*rng, {
            'type': 'cell', 'criteria': op, 'value': value, 'format': cell_fmt,
        })
    elif mode in ('top', 'bottom'):
        try:
            n = int(float(f.get('cond_value') or 10))
        except (TypeError, ValueError):
            n = 10
        n = max(1, n)
        color = f.get('cond_color') or ('#C6EFCE' if mode == 'top' else '#FFC7CE')
        cell_fmt = workbook.add_format({'bg_color': color, 'border': 1, 'bold': True})
        ws.conditional_format(*rng, {
            'type': mode, 'value': n, 'format': cell_fmt,
        })


# ── Chart helpers ───────────────────────────────────────────────────────────

_CHART_TYPES = {'column', 'bar', 'line', 'pie', 'area'}


def _add_chart(workbook, params, sheet_name, headers, data_start, data_end,
               cat_col, value_cols):
    """Insert a chart on a dedicated 'Chart' sheet from data-sheet ranges.

    *value_cols* is a list of column indices to plot as series. *cat_col* is the
    category (label) column index.
    """
    chart_type = (params.get('chart_type') or '').strip()
    if chart_type not in _CHART_TYPES or data_end < data_start or not value_cols:
        return
    chart = workbook.add_chart({'type': chart_type})
    if chart is None:
        return
    for vcol in value_cols:
        series = {
            'name': [sheet_name, _header_row_for(params), vcol],
            'categories': [sheet_name, data_start, cat_col, data_end, cat_col],
            'values': [sheet_name, data_start, vcol, data_end, vcol],
        }
        if chart_type == 'pie':
            series['data_labels'] = {'percentage': True}
        chart.add_series(series)
    chart.set_title({'name': params.get('export_title') or 'Chart'})
    chart.set_size({'width': 900, 'height': 480})
    chart.set_style(10)
    cs = workbook.add_worksheet(_safe_sheet_name('Chart'))
    cs.insert_chart('B2', chart)


def _header_row_for(params):
    return 1 if (params.get('export_title') or '').strip() else 0


# ── Workbook builder (shared by HTTP controller and scheduled cron) ───────────

def build_workbook(env, params):
    """Build an XLSX workbook from *params* and return raw bytes.

    *params* keys: model, field_list, field_type, groupby, domain (list),
    sort_field, sort_dir, limit, bg_color, font_color, export_title, sheet_name,
    freeze_header (bool), alternate_rows (bool), show_totals (bool),
    auto_filter (bool), add_summary_sheet (bool), chart_type, chart_measure.
    """
    if not XLSXWRITER_AVAILABLE:
        raise RuntimeError("xlsxwriter is not installed (pip install xlsxwriter)")

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    _write_flat_sheet(env, workbook, params)
    workbook.close()
    output.seek(0)
    return output.read()


def _build_formats(workbook, params):
    bg = params.get('bg_color') or '#e3e2de'
    fg = params.get('font_color') or '#000000'
    return {
        'header': workbook.add_format({
            'bold': True, 'border': 1, 'bg_color': bg, 'font_color': fg,
            'align': 'center', 'valign': 'vcenter', 'text_wrap': True,
        }),
        'data': workbook.add_format({'border': 1, 'valign': 'vcenter'}),
        'alt': workbook.add_format({'border': 1, 'valign': 'vcenter', 'bg_color': '#F2F6FC'}),
        'date': workbook.add_format({'border': 1, 'valign': 'vcenter', 'num_format': 'yyyy-mm-dd'}),
        'datetime': workbook.add_format({'border': 1, 'valign': 'vcenter', 'num_format': 'yyyy-mm-dd hh:mm'}),
        'date_alt': workbook.add_format({'border': 1, 'valign': 'vcenter', 'num_format': 'yyyy-mm-dd', 'bg_color': '#F2F6FC'}),
        'dtime_alt': workbook.add_format({'border': 1, 'valign': 'vcenter', 'num_format': 'yyyy-mm-dd hh:mm', 'bg_color': '#F2F6FC'}),
        'totals': workbook.add_format({'bold': True, 'border': 1, 'bg_color': '#E8F5E9', 'valign': 'vcenter', 'top': 2}),
        'title': workbook.add_format({
            'bold': True, 'font_size': 13, 'align': 'center', 'valign': 'vcenter',
            'bg_color': '#1a7340', 'font_color': '#ffffff', 'border': 1,
        }),
    }


def _write_cell(ws, fmts, row, col, val, ftype, use_alt, num_fmt_cache=None):
    base = fmts['alt'] if use_alt else fmts['data']
    if ftype == 'date' and val and val is not False:
        fmt = fmts['date_alt'] if use_alt else fmts['date']
        try:
            if isinstance(val, str):
                val = datetime.date.fromisoformat(val)
            ws.write_datetime(row, col, val, fmt)
            return
        except Exception:
            pass
    if ftype == 'datetime' and val and val is not False:
        fmt = fmts['dtime_alt'] if use_alt else fmts['datetime']
        try:
            if isinstance(val, str):
                val = datetime.datetime.fromisoformat(val)
            ws.write_datetime(row, col, val, fmt)
            return
        except Exception:
            pass
    if isinstance(val, bool):
        ws.write(row, col, "Yes" if val else "No", base)
    elif isinstance(val, (int, float)):
        ws.write_number(row, col, val, num_fmt_cache or base)
    else:
        ws.write(row, col, str(val) if val not in (False, None, '') else '', base)


def _write_flat_sheet(env, workbook, params):
    """Standard / grouped report sheet."""
    model = params['model']
    field_list = params.get('field_list') or []
    field_type = params.get('field_type') or 'standard'
    groupby = params.get('groupby') or ''
    fmts = _build_formats(workbook, params)
    ws = workbook.add_worksheet(_safe_sheet_name(params.get('sheet_name')))

    do_freeze = bool(params.get('freeze_header'))
    do_alt = bool(params.get('alternate_rows'))
    do_totals = bool(params.get('show_totals'))

    rows = _collect_export_data(
        env, model, field_list, field_type, groupby,
        params.get('domain') or [], params.get('sort_field') or '',
        params.get('sort_dir') or 'asc', params.get('limit') or 0,
    )

    # Per-column number formats (cached)
    num_fmts = {}
    for i, f in enumerate(field_list):
        if f.get('num_format'):
            num_fmts[i] = workbook.add_format({
                'border': 1, 'valign': 'vcenter', 'num_format': f['num_format'],
            })

    # Optional merged title row above headers
    title_offset = 0
    if (params.get('export_title') or '').strip():
        ws.merge_range(0, 0, 0, max(len(field_list) - 1, 0),
                       params['export_title'].strip(), fmts['title'])
        ws.set_row(0, 28)
        title_offset = 1

    header_row = title_offset
    data_start = title_offset + 1
    if do_freeze:
        ws.freeze_panes(data_start, 0)

    # Auto-fit widths
    col_widths = [max(len(f.get('string', f['name'])), 10) for f in field_list]
    for row_data in rows:
        for i, f in enumerate(field_list):
            col_widths[i] = min(max(col_widths[i], len(str(row_data.get(f['name'], '')))), 60)
    for i, w in enumerate(col_widths):
        ws.set_column(i, i, w + 2)

    ws.set_row(header_row, 22)
    for col, f in enumerate(field_list):
        ws.write(header_row, col, f.get('string', f['name']), fmts['header'])

    for r_idx, row_data in enumerate(rows, start=data_start):
        use_alt = do_alt and (r_idx % 2 == 0)
        for c_idx, f in enumerate(field_list):
            val = row_data.get(f['name'], '')
            _write_cell(ws, fmts, r_idx, c_idx, val, f.get('type', ''), use_alt,
                        num_fmts.get(c_idx))

    data_end = data_start + len(rows) - 1

    if do_totals and rows:
        total_row = data_start + len(rows)
        for c_idx, f in enumerate(field_list):
            transform = f.get('transform')
            if c_idx == 0:
                ws.write(total_row, 0, "TOTAL", fmts['totals'])
            elif transform in ('running_total', 'rank'):
                # A running total's footer is its last value; rank has no total.
                ws.write(total_row, c_idx, '', fmts['totals'])
            elif f.get('type') in _NUMERIC_TYPES or f['name'] == '__count':
                # pct_of_total falls through here too — summing the percentage
                # cells yields ~100 (or 0 when the column had no data).
                total_val = sum(
                    r.get(f['name'], 0) or 0 for r in rows
                    if isinstance(r.get(f['name'], 0), (int, float))
                    and not isinstance(r.get(f['name'], 0), bool)
                )
                ws.write_number(total_row, c_idx, total_val, fmts['totals'])
            else:
                ws.write(total_row, c_idx, '', fmts['totals'])

    # Excel AutoFilter on the header + data range (native sort/filter dropdowns)
    if params.get('auto_filter') and rows and field_list:
        ws.autofilter(header_row, 0, data_end, len(field_list) - 1)

    # Conditional formatting (per column)
    if rows:
        for c_idx, f in enumerate(field_list):
            if f.get('type') in _NUMERIC_TYPES:
                _apply_conditional_format(workbook, ws, f, data_start, data_end, c_idx)

    # Chart
    if rows and (params.get('chart_type') or '') in _CHART_TYPES:
        cat_col, value_cols = _resolve_chart_columns(field_list, params, field_type, groupby)
        if value_cols:
            _add_chart(workbook, params, ws.name, None, data_start, data_end,
                       cat_col, value_cols)

    if params.get('add_summary_sheet') and rows:
        _write_summary_sheet(workbook, model, field_list, rows)


def _resolve_chart_columns(field_list, params, field_type, groupby):
    """Pick the category column and value column(s) for charting a flat sheet."""
    cat_col = 0
    measure = params.get('chart_measure') or ''
    value_cols = []
    if measure:
        for i, f in enumerate(field_list):
            if f['name'] == measure:
                value_cols = [i]
                break
    if not value_cols:
        # Fall back to every numeric column (skip the first if it's the dimension)
        for i, f in enumerate(field_list):
            if i == cat_col:
                continue
            if f.get('type') in _NUMERIC_TYPES or f['name'] == '__count':
                value_cols.append(i)
    return cat_col, value_cols


def _write_summary_sheet(workbook, model, field_list, rows):
    import datetime as _dt
    sw = workbook.add_worksheet("Summary")
    t_fmt = workbook.add_format({'bold': True, 'font_size': 12,
                                 'bg_color': '#1a7340', 'font_color': '#fff', 'border': 1})
    h_fmt = workbook.add_format({'bold': True, 'bg_color': '#f0f0f0', 'border': 1})
    v_fmt = workbook.add_format({'border': 1, 'num_format': '#,##0.00'})
    l_fmt = workbook.add_format({'bold': True, 'border': 1})

    sw.merge_range(0, 0, 0, 4, f"Summary — {model}", t_fmt)
    sw.write(1, 0, "Exported on", h_fmt)
    sw.write(1, 1, _dt.datetime.now().strftime('%Y-%m-%d %H:%M'), l_fmt)
    sw.write(2, 0, "Total rows", h_fmt)
    sw.write_number(2, 1, len(rows), l_fmt)
    sw.write(4, 0, "Column", h_fmt)
    sw.write(4, 1, "Sum", h_fmt)
    sw.write(4, 2, "Average", h_fmt)
    sw.write(4, 3, "Max", h_fmt)
    sw.write(4, 4, "Min", h_fmt)

    stat_row = 5
    for f in field_list:
        if f.get('type') not in _NUMERIC_TYPES:
            continue
        vals = [
            r.get(f['name'], 0) or 0 for r in rows
            if isinstance(r.get(f['name'], 0), (int, float))
            and not isinstance(r.get(f['name'], 0), bool)
        ]
        if not vals:
            continue
        sw.write(stat_row, 0, f.get('string', f['name']), l_fmt)
        sw.write_number(stat_row, 1, sum(vals), v_fmt)
        sw.write_number(stat_row, 2, sum(vals) / len(vals), v_fmt)
        sw.write_number(stat_row, 3, max(vals), v_fmt)
        sw.write_number(stat_row, 4, min(vals), v_fmt)
        stat_row += 1

    sw.set_column(0, 0, 30)
    sw.set_column(1, 4, 16)


class ExportController(http.Controller):

    def _parse_request_params(self, fields, ids, domain, sort_field, sort_dir, export_limit):
        try:
            field_list = json.loads(fields)
        except (json.JSONDecodeError, TypeError):
            field_list = []
        final_domain = parse_domain(request.env, domain)
        if ids:
            record_ids = [int(x) for x in ids.split(',') if x.strip().isdigit()]
            if record_ids:
                final_domain = [('id', 'in', record_ids)] + list(final_domain)
        return field_list, final_domain

    @staticmethod
    def _error_response(message):
        return request.make_response(
            "<html><body><h2>%s</h2></body></html>" % escape(message),
            headers=[('Content-Type', 'text/html; charset=utf-8')], status=400,
        )

    @http.route('/web/export/dynamic_xlsx', type='http', auth='user')
    def export_xlsx(self, model, fields, ids=None, domain="[]",
                    bg_color="#e3e2de", font_color="#000000", field_type='standard',
                    groupby='', filename='', sheet_name='Data', freeze_header='1',
                    alternate_rows='0', show_totals='0', export_title='',
                    sort_field='', sort_dir='asc', limit='0', add_summary_sheet='0',
                    chart_type='', chart_measure='', auto_filter='0', **kw):

        if not XLSXWRITER_AVAILABLE:
            return request.make_response(
                "<html><body><h2>xlsxwriter not installed. Run: <code>pip install xlsxwriter</code></h2></body></html>",
                headers=[('Content-Type', 'text/html; charset=utf-8')], status=500,
            )

        try:
            field_list, final_domain = self._parse_request_params(
                fields, ids, domain, sort_field, sort_dir, limit)
        except UserError as err:
            return self._error_response(err.args[0])

        params = {
            'model': model, 'field_list': field_list, 'field_type': field_type,
            'groupby': groupby, 'domain': final_domain,
            'sort_field': sort_field, 'sort_dir': sort_dir, 'limit': limit,
            'bg_color': bg_color, 'font_color': font_color,
            'export_title': export_title, 'sheet_name': sheet_name,
            'freeze_header': freeze_header == '1',
            'alternate_rows': alternate_rows == '1',
            'show_totals': show_totals == '1',
            'auto_filter': auto_filter == '1',
            'add_summary_sheet': add_summary_sheet == '1',
            'chart_type': chart_type, 'chart_measure': chart_measure,
        }
        data = build_workbook(request.env, params)
        safe_name = _sanitize_filename(filename, f"report_{model}")
        return request.make_response(
            data,
            headers=[
                ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                ('Content-Disposition', f'attachment; filename="{safe_name}.xlsx"'),
            ],
        )

    @http.route('/web/export/dynamic_csv', type='http', auth='user')
    def export_csv(self, model, fields, ids=None, domain="[]",
                   field_type='standard', groupby='',
                   sort_field='', sort_dir='asc', limit='0', filename='', **kw):

        try:
            field_list, final_domain = self._parse_request_params(
                fields, ids, domain, sort_field, sort_dir, limit)
        except UserError as err:
            return self._error_response(err.args[0])
        rows = _collect_export_data(
            request.env, model, field_list, field_type, groupby,
            final_domain, sort_field, sort_dir, limit,
        )

        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_ALL)
        writer.writerow([f.get('string', f['name']) for f in field_list])
        for row in rows:
            writer.writerow([
                str(row.get(f['name'], '') or '') for f in field_list
            ])

        content = output.getvalue()
        safe_name = _sanitize_filename(filename, f"report_{model}")
        return request.make_response(
            # UTF-8 BOM so Excel auto-detects encoding
            b'\xef\xbb\xbf' + content.encode('utf-8'),
            headers=[
                ('Content-Type', 'text/csv; charset=utf-8'),
                ('Content-Disposition', f'attachment; filename="{safe_name}.csv"'),
            ],
        )
