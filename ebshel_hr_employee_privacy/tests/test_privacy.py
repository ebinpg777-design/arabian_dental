# Part of ebshel_hr_employee_privacy. See LICENSE file for full copyright and licensing details.
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestEmployeePrivacy(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plain = new_test_user(cls.env, login="privacy_plain", groups="base.group_user")
        cls.officer = new_test_user(cls.env, login="privacy_officer", groups="base.group_user,hr.group_hr_user")
        cls.admin = new_test_user(cls.env, login="privacy_admin", groups="base.group_user,base.group_system")
        Employee = cls.env["hr.employee"]
        cls.own = Employee.create({"name": "Plain Person", "user_id": cls.plain.id})
        cls.officer_employee = Employee.create({"name": "The Officer", "user_id": cls.officer.id})
        cls.other = Employee.create({"name": "Somebody Else"})

    def _directory(self, user):
        return self.env["hr.employee.public"].with_user(user)

    def test_a_user_without_employees_access_lists_only_themselves(self):
        directory = self._directory(self.plain)
        self.assertEqual(directory.search([]).mapped("name"), ["Plain Person"])
        self.assertEqual(directory.search_count([]), 1)
        self.assertEqual([name for _id, name in directory.name_search("")], ["Plain Person"])
        self.assertEqual([name for _id, name in directory.name_search("Somebody")], [])
        groups = directory.read_group([], ["department_id"], ["__count"])
        self.assertEqual(sum(count for _department, count in groups), 1)

    def test_a_colleague_named_on_a_record_still_reads(self):
        # narrowed lists, not blocked reads: what a work order or a desk shows stays shown
        other = self._directory(self.plain).browse(self.other.id)
        self.assertEqual(other.name, "Somebody Else")

    def test_an_employees_officer_sees_everyone(self):
        names = self._directory(self.officer).search([]).mapped("name")
        for name in ("Plain Person", "The Officer", "Somebody Else"):
            self.assertIn(name, names)

    def test_a_system_administrator_is_not_restricted(self):
        names = self._directory(self.admin).search([]).mapped("name")
        for name in ("Plain Person", "Somebody Else"):
            self.assertIn(name, names)

    def test_a_user_without_an_employee_sees_nobody(self):
        lonely = new_test_user(self.env, login="privacy_lonely", groups="base.group_user")
        self.assertEqual(self._directory(lonely).search([]).mapped("name"), [])
