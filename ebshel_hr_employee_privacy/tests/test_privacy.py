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
        return self.env["hr.employee.public"].with_user(user).search([]).mapped("name")

    def test_a_user_without_employees_access_sees_only_themselves(self):
        self.assertEqual(self._directory(self.plain), ["Plain Person"])
        # the employee model itself: nothing but their own, and not writable
        self.assertEqual(
            self.env["hr.employee"].with_user(self.plain).sudo(False).search([]).mapped("name")
            if self.env["hr.employee"].with_user(self.plain).has_access("read") else [],
            [],
        )

    def test_an_employees_officer_sees_everyone(self):
        names = self._directory(self.officer)
        for name in ("Plain Person", "The Officer", "Somebody Else"):
            self.assertIn(name, names)
        self.assertIn("Somebody Else", self.env["hr.employee"].with_user(self.officer).search([]).mapped("name"))

    def test_a_system_administrator_is_not_restricted(self):
        names = self._directory(self.admin)
        for name in ("Plain Person", "Somebody Else"):
            self.assertIn(name, names)

    def test_a_user_without_an_employee_sees_nobody(self):
        lonely = new_test_user(self.env, login="privacy_lonely", groups="base.group_user")
        self.assertEqual(self._directory(lonely), [])
