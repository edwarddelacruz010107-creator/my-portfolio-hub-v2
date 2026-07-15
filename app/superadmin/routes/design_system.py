"""Superadmin-only design-system reference surface."""
from flask import render_template

from app.superadmin.blueprint import superadmin, superadmin_required


@superadmin.route('/design-system')
@superadmin_required
def design_system_reference():
    """Render synthetic UI labels only; never query or invent business data."""
    return render_template('superadmin/design_system_reference.html')


@superadmin.route('/component-system')
@superadmin_required
def component_system_reference():
    """Render the shared component contract with synthetic content only."""
    return render_template('superadmin/component_system_reference.html')
