# Part of ebshel_invoice_analysis_fix. See LICENSE file for full copyright and licensing details.
from odoo.addons.account.tests.common import AccountTestInvoicingHttpCommon
from odoo.tests import tagged

# Switch the Invoices Analysis to its pivot and wait for an amount in a cell.
PIVOT_HAS_AMOUNTS = """
const deadline = Date.now() + 20000;
(function check() {
    const pivotButton = document.querySelector('.o_switch_view.o_pivot');
    if (pivotButton && !document.querySelector('.o_pivot_view')) {
        pivotButton.click();
    }
    const cells = [...document.querySelectorAll('.o_pivot_view .o_pivot_cell_value')];
    const filled = cells.filter((cell) => cell.textContent.trim());
    if (cells.length && filled.length === cells.length) {
        console.log('test successful');
    } else if (Date.now() > deadline) {
        console.error(`pivot cells: ${cells.length}, with an amount: ${filled.length}`);
    } else {
        setTimeout(check, 250);
    }
})();
"""


@tagged('post_install', '-at_install')
class TestPivotInBrowser(AccountTestInvoicingHttpCommon):

    def test_invoices_analysis_pivot_shows_amounts(self):
        self.init_invoice('out_invoice', amounts=[1000.0], post=True)
        self.browser_js(
            '/odoo/action-account.action_account_invoice_report_all',
            PIVOT_HAS_AMOUNTS,
            ready="!!document.querySelector('.o_switch_view.o_pivot')",
            login=self.env.user.login,
        )
