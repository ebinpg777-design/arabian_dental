# -*- coding: utf-8 -*-
"""Every product the lab makes is made on its category's route (models/category_boms.py)."""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCategoryBoms(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Loader = cls.env['lab.masters.loader']
        Category = cls.env['product.category']

        def category(name):
            return Category.search([('name', '=ilike', name)], limit=1) or Category.create({'name': name})
        cls.cat = {name: category(name) for name in (
            'ZIRCONIA', 'METAL CERAMIC', 'ACRYLIC', 'ORTHODONTIC', 'IMPLANT PROSTHEIS', 'CERAMIC')}
        cls.other = Category.create({'name': 'ZZ OFFICE THINGS'})

    def _service(self, name, category):
        return self.env['product.template'].create({
            'name': name, 'type': 'service', 'sale_ok': True, 'list_price': 100,
            'categ_id': (self.cat.get(category) or category).id})

    def _route(self, product):
        return product.bom_ids.operation_ids.sorted('sequence').mapped('workcenter_id.name')

    def test_each_product_follows_its_category(self):
        cases = {
            'ZZ PREMIUM CERAMIC': ('METAL CERAMIC', 'Casting'),
            'ZZ MONOLITH': ('ZIRCONIA', 'Milling'),
            'ZZ BEGGS': ('ORTHODONTIC', 'Wire Bending'),
            'ZZ FLEXIBLE RPD': ('ACRYLIC', 'Flasking'),
            'ZZ RPD REPAIR': ('ACRYLIC', 'Flasking'),        # repairs: the full route
        }
        products = {name: self._service(name, category) for name, (category, _bench) in cases.items()}
        self.Loader.make_category_boms(self.env['product.template'].browse(
            [p.id for p in products.values()]))
        for name, (_category, bench) in cases.items():
            product = products[name]
            self.assertEqual(len(product.bom_ids), 1, name)
            self.assertIn(bench, self._route(product), name)
            self.assertEqual((product.type, product.is_storable), ('consu', False), name)
            self.assertIn(self.env.ref('stock.route_warehouse0_mto'), product.route_ids, name)
            self.assertFalse(product.bom_ids.bom_line_ids, "steps only, no components")

    def test_what_gets_no_job(self):
        products = [self._service(name, category) for name, category in (
            ('ZZ RPD ADDITIONAL PER UNIT', 'ACRYLIC'), ('ZZ TOOTH ADDING', 'ACRYLIC'),
            ('ZZ COURIER CHARGES', 'ZIRCONIA'), ('ZZ CERAMIC (MATERIAL COST)', 'METAL CERAMIC'),
            ('ZZ FACE BOW KIT', 'ORTHODONTIC'), ('ZZ CUSTOMISED ABUTMENT', 'IMPLANT PROSTHEIS'),
            ('ZZ COMPOSITE MOCKUP', 'CERAMIC'), ('ZZ STAPLER', self.other))]
        result = self.Loader.make_category_boms(self.env['product.template'].concat(*products))
        self.assertFalse(result['made'])
        for product in products:
            self.assertFalse(product.bom_ids, product.name)
            self.assertEqual(product.type, 'service', "%s is left as it was" % product.name)

    def test_the_routes_that_are_not_a_whole_sheet(self):
        model = self._service('ZZ PRINTED MODEL', 'ACRYLIC')
        tray = self._service('ZZ CD SPECIAL TRAY PER JAW', 'ACRYLIC')
        bite = self._service('ZZ CD BITE REGISTRATION PER JAW', 'ACRYLIC')
        essix = self._service('ZZ ESSIX HARD SPLINT', 'ORTHODONTIC')
        rme = self._service('ZZ BONDED RME WITH REVERSE PULL HEADGEAR KIT', 'ORTHODONTIC')
        implant = self._service('ZZ SCREW RETAINED IMPLANT', 'IMPLANT PROSTHEIS')
        coping = self._service('ZZ METAL COPING', 'CERAMIC')
        self.Loader.make_category_boms(model | tray | bite | essix | rme | implant | coping)
        # cured, then prepared like any model - at its own department's bench
        self.assertEqual(self._route(model), ['Scanning', 'Designing', 'Printing', 'Light Curing',
                                              'Acrylic/Model Preparation', 'Final Check', 'Packing'])
        self.assertEqual(self._route(tray), ['Acrylic/Model Preparation', 'Special Tray',
                                             'Final Check', 'Packing'])
        self.assertEqual(self._route(bite), ['Acrylic/Model Preparation', 'Wax Bite',
                                             'Final Check', 'Packing'])
        self.assertNotIn('Wire Bending', self._route(essix))
        self.assertIn('Wire Bending', self._route(rme), "an RME is made, its kit aside")
        self.assertIn('Milling', self._route(implant), "an implant crown: the zirconia route")
        self.assertIn('Casting', self._route(coping), "metal: the full metal route")

    def test_a_second_run_leaves_a_product_with_a_bom_alone(self):
        product = self._service('ZZ GINGIVAL CERAMIC', 'METAL CERAMIC')
        self.Loader.make_category_boms(product)
        bom = product.bom_ids
        result = self.Loader.make_category_boms(product)
        self.assertEqual(product.bom_ids, bom)
        self.assertFalse(result['made'])

    def test_an_order_for_it_raises_its_job_on_its_route(self):
        product = self._service('ZZ BEGGS PER JAW', 'ORTHODONTIC')
        self.Loader.make_category_boms(product)
        partner = self.env['res.partner'].create({'name': 'ZZ Category Clinic'})
        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': product.product_variant_id.id,
                                   'product_uom_qty': 1})]})
        order.action_confirm()
        jobs = self.env['mrp.production'].search([('sale_id', '=', order.id)])
        self.assertEqual(len(jobs), 1, "the order raised its job")
        self.assertEqual(jobs.workorder_ids.sorted('sequence').mapped('workcenter_id.name'),
                         ['Orthodontic/Model Preparation', 'Wire Bending', 'Acrylization',
                          'Orthodontic/Trimming', 'Final Check', 'Packing'])

    def test_case_cost_shows_only_the_costed_routes(self):
        product = self._service('ZZ PMMA CROWN', 'METAL CERAMIC')
        self.Loader.make_category_boms(product)
        rows = self.env['lab.case.cost'].get_routes()['rows']
        self.assertNotIn(product.bom_ids.id, [row['bom_id'] for row in rows])
