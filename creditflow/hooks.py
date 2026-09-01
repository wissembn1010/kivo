app_name = "creditflow"
app_title = "CreditFlow"
app_publisher = "Wissen Benali"
app_description = "Customer credit, payment and debt management system"
app_email = "wissemwork10@gmail.com"
app_license = "mit"

fixtures = [
	{
		"dt": "Role",
		"filters": [["name", "in", ["OWNER", "STAFF"]]],
	},
	{
		"dt": "Custom Field",
		"filters": [["name", "=", "User-creditflow_business"]],
	},
]

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "creditflow",
# 		"logo": "/assets/creditflow/logo.png",
# 		"title": "CreditFlow",
# 		"route": "/creditflow",
# 		"has_permission": "creditflow.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
app_include_css = "/assets/creditflow/css/saas-access.css"
app_include_js = "/assets/creditflow/js/saas-access.js"

# include js, css files in header of web template
# web_include_css = "/assets/creditflow/css/creditflow.css"
# web_include_js = "/assets/creditflow/js/creditflow.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "creditflow/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"Business": "public/js/business_onboarding.js",
	"Customer": "public/js/business_defaults.js",
	"Product": "public/js/business_defaults.js",
	"Supplier": "public/js/business_defaults.js",
"Supplier Payment": "public/js/business_defaults.js",
"Supplier Transaction": "public/js/business_defaults.js",
"Sale": "public/js/business_defaults.js",
"Payment": "public/js/business_defaults.js",
"Purchase": "public/js/business_defaults.js",
"Sale Reversal": "public/js/business_defaults.js",
"Sale Return": "public/js/business_defaults.js",
"Payment Reversal": "public/js/business_defaults.js",
"Purchase Reversal": "public/js/business_defaults.js",
"Supplier Payment Reversal": "public/js/business_defaults.js",
"TEJ Export Batch": "public/js/business_defaults.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "creditflow/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (dynamic for verified self-service tenants)
get_website_user_home_page = "creditflow.saas_onboarding.get_creditflow_home_page"
extend_bootinfo = "creditflow.saas_access.extend_bootinfo"

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "creditflow.utils.jinja_methods",
# 	"filters": "creditflow.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "creditflow.install.before_install"
after_install = "creditflow.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "creditflow.uninstall.before_uninstall"
# after_uninstall = "creditflow.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "creditflow.utils.before_app_install"
# after_app_install = "creditflow.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "creditflow.utils.before_app_uninstall"
# after_app_uninstall = "creditflow.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "creditflow.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "creditflow.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

permission_query_conditions = {
	doctype: "creditflow.permissions.get_permission_query_conditions"
	for doctype in (
		"Business",
		"Customer",
		"Product",
		"Supplier",
		"Supplier Payment",
		"Supplier Transaction",
		"Sale",
		"Payment",
		"Purchase",
		"Sale Reversal",
		"Sale Return",
		"Onboarding Import Batch",
		"Payment Reversal",
		"Purchase Reversal",
		"Supplier Payment Reversal",
		"Credit Transaction",
		"Stock Movement",
		"CreditFlow Subscription",
		"CreditFlow Billing Payment",
		"Withholding Tax",
		"TEJ Export Batch",
	)
}

has_permission = {
	doctype: "creditflow.permissions.has_permission"
	for doctype in permission_query_conditions
}

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	doctype: {
		"before_validate": "creditflow.permissions.set_and_validate_business",
	}
	for doctype in permission_query_conditions
}
doc_events["User"] = {"before_validate": "creditflow.permissions.enforce_user_business_limit"}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"creditflow.tasks.all"
# 	],
# 	"daily": [
# 		"creditflow.tasks.daily"
# 	],
# 	"hourly": [
# 		"creditflow.tasks.hourly"
# 	],
# 	"weekly": [
# 		"creditflow.tasks.weekly"
# 	],
# 	"monthly": [
# 		"creditflow.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "creditflow.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "creditflow.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "creditflow.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "creditflow.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["creditflow.utils.before_request"]
# after_request = ["creditflow.utils.after_request"]

# Job Events
# ----------
# before_job = ["creditflow.utils.before_job"]
# after_job = ["creditflow.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"creditflow.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
