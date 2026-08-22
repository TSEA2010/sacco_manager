import frappe

FOOTER_HTML = """
<div style="text-align:center; font-size:9px; color:#888; margin-top:10px;">
    Powered By TSEA ERP @ www.tsea.co.ke
</div>
"""

def _patch_get_rendered_template():
    try:
        import frappe.www.printview as printview_module
    except ImportError:
        frappe.logger().warning("csf_ke: could not import frappe.www.printview")
        return

    if not hasattr(printview_module, "get_rendered_template"):
        frappe.logger().warning("csf_ke: printview has no get_rendered_template to patch")
        return

    _original = printview_module.get_rendered_template

    def patched(*args, **kwargs):
        html = _original(*args, **kwargs)
        if isinstance(html, str) and "Powered By TSEA ERP" not in html:
            html += FOOTER_HTML
        return html

    printview_module.get_rendered_template = patched

_patch_get_rendered_template()
