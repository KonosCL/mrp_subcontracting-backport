# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.tests import Form
from odoo.tests.common import SavepointCase, TransactionCase

from odoo.tests import tagged

@tagged('post_install', '-at_install')
class TestSubcontractingBasic(TransactionCase):
    def test_subcontracting_location_1(self):
        """ Checks the creation and presence of the subcontracting location. """
        self.assertTrue(self.env.company.subcontracting_location_id)
        self.assertTrue(self.env.company.subcontracting_location_id.active)
        company2 = self.env['res.company'].create({'name': 'Test Company'})
        self.assertTrue(company2.subcontracting_location_id)
        self.assertTrue(self.env.company.subcontracting_location_id != company2.subcontracting_location_id)

class TestSubcontractingFlows(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super(TestSubcontractingFlows, cls).setUpClass()
        # 1: Create a subcontracting partner
        main_partner = cls.env['res.partner'].create({'name': 'main_partner'})
        cls.subcontractor_partner1 = cls.env['res.partner'].create({
            'name': 'subcontractor_partner',
            'parent_id': main_partner.id,
            'company_id': cls.env.ref('base.main_company').id
        })

        # 2. Create a BOM of subcontracting type
        cls.comp1 = cls.env['product.product'].create({
            'name': 'Component1',
            'type': 'product',
            'categ_id': cls.env.ref('product.product_category_all').id,
        })
        cls.comp2 = cls.env['product.product'].create({
            'name': 'Component2',
            'type': 'product',
            'categ_id': cls.env.ref('product.product_category_all').id,
        })
        cls.finished = cls.env['product.product'].create({
            'name': 'finished',
            'type': 'product',
            'categ_id': cls.env.ref('product.product_category_all').id,
        })
        bom_form = Form(cls.env['mrp.bom'])
        bom_form.type = 'subcontract'
        bom_form.product_tmpl_id = cls.finished.product_tmpl_id
        with bom_form.bom_line_ids.new() as bom_line:
            bom_line.product_id = cls.comp1
            bom_line.product_qty = 1
        with bom_form.bom_line_ids.new() as bom_line:
            bom_line.product_id = cls.comp2
            bom_line.product_qty = 1
        cls.bom = bom_form.save()

        # Create a BoM for cls.comp2
        cls.comp2comp = cls.env['product.product'].create({
            'name': 'component for Component2',
            'type': 'product',
            'categ_id': cls.env.ref('product.product_category_all').id,
        })
        bom_form = Form(cls.env['mrp.bom'])
        bom_form.product_tmpl_id = cls.comp2.product_tmpl_id
        with bom_form.bom_line_ids.new() as bom_line:
            bom_line.product_id = cls.comp2comp
            bom_line.product_qty = 1
        cls.comp2_bom = bom_form.save()

    def test_flow_1(self):
        """ Don't tick any route on the components and trigger the creation of the subcontracting
        manufacturing order through a receipt picking. Create a reordering rule in the
        subcontracting locations for a component and run the scheduler to resupply. Checks if the
        resupplying actually works
        """
        # Create a receipt picking from the subcontractor
        picking_form = Form(self.env['stock.picking'])
        picking_form.picking_type_id = self.env.ref('stock.picking_type_in')
        picking_form.partner_id = self.subcontractor_partner1
        with picking_form.move_ids_without_package.new() as move:
            move.product_id = self.finished
            move.product_uom_qty = 1
        picking_receipt = picking_form.save()
        picking_receipt.action_confirm()

        # Nothing should be tracked
        self.assertFalse(picking_receipt.display_action_record_components)

        # Check the created manufacturing order
        mo = self.env['mrp.production'].search([('bom_id', '=', self.bom.id)])
        self.assertEqual(len(mo), 1)
        self.assertEqual(len(mo.picking_ids), 0)
        wh = picking_receipt.picking_type_id.warehouse_id
        self.assertEquals(mo.picking_type_id, wh.subcontracting_type_id)
        self.assertFalse(mo.picking_type_id.active)

        # Create a RR
        pg1 = self.env['procurement.group'].create({})
        self.env['stock.warehouse.orderpoint'].create({
            'name': 'xxx',
            'product_id': self.comp1.id,
            'product_min_qty': 0,
            'product_max_qty': 0,
            'location_id': self.env.user.company_id.subcontracting_location_id.id,
            'group_id': pg1.id,
        })

        # Run the scheduler and check the created picking
        self.env['procurement.group'].run_scheduler()
        picking = self.env['stock.picking'].search([('group_id', '=', pg1.id)])
        self.assertEqual(len(picking), 1)
        self.assertEquals(picking.picking_type_id, wh.out_type_id)
        picking_receipt.move_lines.quantity_done = 1
        picking_receipt.button_validate()
        self.assertEquals(mo.state, 'done')

        # Available quantities should be negative at the subcontracting location for each components
        avail_qty_comp1 = self.env['stock.quant']._get_available_quantity(self.comp1, self.subcontractor_partner1.property_stock_subcontractor, allow_negative=True)
        avail_qty_comp2 = self.env['stock.quant']._get_available_quantity(self.comp2, self.subcontractor_partner1.property_stock_subcontractor, allow_negative=True)
        avail_qty_finished = self.env['stock.quant']._get_available_quantity(self.finished, wh.lot_stock_id)
        self.assertEquals(avail_qty_comp1, -1)
        self.assertEquals(avail_qty_comp2, -1)
        self.assertEquals(avail_qty_finished, 1)

    def test_flow_2(self):
        """ Tick "Resupply Subcontractor on Order" on the components and trigger the creation of
        the subcontracting manufacturing order through a receipt picking. Checks if the resupplying
        actually works. Also set a different subcontracting location on the partner.
        """
        # Tick "resupply subconractor on order"
        resupply_sub_on_order_route = self.env['stock.location.route'].search([('name', '=', 'Resupply Subcontractor on Order')])
        (self.comp1 + self.comp2).write({'route_ids': [(4, resupply_sub_on_order_route.id, None)]})
        # Create a different subcontract location
        partner_subcontract_location = self.env['stock.location'].create({
            'name': 'Specific partner location',
            'location_id': self.env.ref('stock.stock_location_locations_partner').id,
            'usage': 'internal',
            'company_id': self.env.company.id,
        })
        self.subcontractor_partner1.property_stock_subcontractor = partner_subcontract_location.id
        resupply_rule = resupply_sub_on_order_route.rule_ids.filtered(lambda l:
            l.location_id == self.comp1.property_stock_production and
            l.location_src_id == self.env.company.subcontracting_location_id)
        resupply_rule.copy({'location_src_id': partner_subcontract_location.id})
        resupply_warehouse_rule = self.warehouse.route_ids.rule_ids.filtered(lambda l:
            l.location_id == self.env.company.subcontracting_location_id and
            l.location_src_id == self.warehouse.lot_stock_id)
        resupply_warehouse_rule.copy({'location_id': partner_subcontract_location.id})

        # Create a receipt picking from the subcontractor
        picking_form = Form(self.env['stock.picking'])
        picking_form.picking_type_id = self.env.ref('stock.picking_type_in')
        picking_form.partner_id = self.subcontractor_partner1
        with picking_form.move_ids_without_package.new() as move:
            move.product_id = self.finished
            move.product_uom_qty = 1
        picking_receipt = picking_form.save()
        picking_receipt.action_confirm()

        # Nothing should be tracked
        self.assertFalse(picking_receipt.display_action_record_components)

        # Pickings should directly be created
        mo = self.env['mrp.production'].search([('bom_id', '=', self.bom.id)])
        self.assertEqual(len(mo.picking_ids), 1)
        self.assertEquals(mo.state, 'to_close')
        self.assertEqual(len(mo.picking_ids.move_lines), 2)

        picking = mo.picking_ids
        wh = picking.picking_type_id.warehouse_id

        # The picking should be a delivery order
        self.assertEquals(picking.picking_type_id, wh.out_type_id)

        self.assertEquals(mo.picking_type_id, wh.subcontracting_type_id)
        self.assertFalse(mo.picking_type_id.active)

        # No manufacturing order for `self.comp2`
        comp2mo = self.env['mrp.production'].search([('bom_id', '=', self.comp2_bom.id)])
        self.assertEqual(len(comp2mo), 0)

        picking_receipt.move_lines.quantity_done = 1
        picking_receipt.button_validate()
        self.assertEquals(mo.state, 'done')

        # Available quantities should be negative at the subcontracting location for each components
        avail_qty_comp1 = self.env['stock.quant']._get_available_quantity(self.comp1, self.subcontractor_partner1.property_stock_subcontractor, allow_negative=True)
        avail_qty_comp2 = self.env['stock.quant']._get_available_quantity(self.comp2, self.subcontractor_partner1.property_stock_subcontractor, allow_negative=True)
        avail_qty_finished = self.env['stock.quant']._get_available_quantity(self.finished, wh.lot_stock_id)
        self.assertEquals(avail_qty_comp1, -1)
        self.assertEquals(avail_qty_comp2, -1)
        self.assertEquals(avail_qty_finished, 1)

        avail_qty_comp1_in_global_location = self.env['stock.quant']._get_available_quantity(self.comp1, self.env.company.subcontracting_location_id, allow_negative=True)
        avail_qty_comp2_in_global_location = self.env['stock.quant']._get_available_quantity(self.comp2, self.env.company.subcontracting_location_id, allow_negative=True)
        self.assertEqual(avail_qty_comp1_in_global_location, 0.0)
        self.assertEqual(avail_qty_comp2_in_global_location, 0.0)

    def test_flow_3(self):
        """ Tick "Resupply Subcontractor on Order" and "MTO" on the components and trigger the
        creation of the subcontracting manufacturing order through a receipt picking. Checks if the
        resupplying actually works. One of the component has also "manufacture" set and a BOM
        linked. Checks that an MO is created for this one.
        """
        # Tick "resupply subconractor on order"
        resupply_sub_on_order_route = self.env['stock.location.route'].search([('name', '=', 'Resupply Subcontractor on Order')])
        (self.comp1 + self.comp2).write({'route_ids': [(4, resupply_sub_on_order_route.id, None)]})

        # Tick "manufacture" and MTO on self.comp2
        mto_route = self.env['stock.location.route'].search([('name', '=', 'Replenish on Order (MTO)')])
        manufacture_route = self.env['stock.location.route'].search([('name', '=', 'Manufacture')])
        self.comp2.write({'route_ids': [(4, manufacture_route.id, None)]})
        self.comp2.write({'route_ids': [(4, mto_route.id, None)]})

        # Create a receipt picking from the subcontractor
        picking_form = Form(self.env['stock.picking'])
        picking_form.picking_type_id = self.env.ref('stock.picking_type_in')
        picking_form.partner_id = self.subcontractor_partner1
        with picking_form.move_ids_without_package.new() as move:
            move.product_id = self.finished
            move.product_uom_qty = 1
        picking_receipt = picking_form.save()
        picking_receipt.action_confirm()

        # Nothing should be tracked
        self.assertFalse(picking_receipt.display_action_record_components)

        # Pickings should directly be created
        mo = self.env['mrp.production'].search([('bom_id', '=', self.bom.id)])
        self.assertEqual(len(mo.picking_ids), 1)
        self.assertEquals(mo.state, 'to_close')
        self.assertEqual(len(mo.picking_ids.move_lines), 2)

        # The picking should be a delivery order
        wh = picking_receipt.picking_type_id.warehouse_id
        self.assertEquals(mo.picking_ids.picking_type_id, wh.out_type_id)

        self.assertEquals(mo.picking_type_id, wh.subcontracting_type_id)
        self.assertFalse(mo.picking_type_id.active)

        # As well as a manufacturing order for `self.comp2`
        comp2mo = self.env['mrp.production'].search([('bom_id', '=', self.comp2_bom.id)])
        self.assertEqual(len(comp2mo), 1)
        picking_receipt.move_lines.quantity_done = 1
        picking_receipt.button_validate()
        self.assertEquals(mo.state, 'done')

        # Available quantities should be negative at the subcontracting location for each components
        avail_qty_comp1 = self.env['stock.quant']._get_available_quantity(self.comp1, self.subcontractor_partner1.property_stock_subcontractor, allow_negative=True)
        avail_qty_comp2 = self.env['stock.quant']._get_available_quantity(self.comp2, self.subcontractor_partner1.property_stock_subcontractor, allow_negative=True)
        avail_qty_finished = self.env['stock.quant']._get_available_quantity(self.finished, wh.lot_stock_id)
        self.assertEquals(avail_qty_comp1, -1)
        self.assertEquals(avail_qty_comp2, -1)
        self.assertEquals(avail_qty_finished, 1)

    def test_flow_4(self):
        """ Check that the correct BoM is chosen accordingly to the partner
        """
        # We create a second partner of type subcontractor
        main_partner_2 = self.env['res.partner'].create({'name': 'main_partner'})
        subcontractor_partner2 = self.env['res.partner'].create({
            'name': 'subcontractor_partner',
            'parent_id': main_partner_2.id,
            'company_id': self.env.ref('base.main_company').id
        })

        # We create a different BoM for the same product
        comp3 = self.env['product.product'].create({
            'name': 'Component1',
            'type': 'product',
            'categ_id': self.env.ref('product.product_category_all').id,
        })

        bom_form = Form(self.env['mrp.bom'])
        bom_form.type = 'subcontract'
        bom_form.product_tmpl_id = self.finished.product_tmpl_id
        with bom_form.bom_line_ids.new() as bom_line:
            bom_line.product_id = self.comp1
            bom_line.product_qty = 1
        with bom_form.bom_line_ids.new() as bom_line:
            bom_line.product_id = comp3
            bom_line.product_qty = 1
        bom2 = bom_form.save()

        # We assign the second BoM to the new partner
        self.bom.write({'subcontractor_ids': [(4, self.subcontractor_partner1.id, None)]})
        bom2.write({'subcontractor_ids': [(4, subcontractor_partner2.id, None)]})

        # Create a receipt picking from the subcontractor1
        picking_form = Form(self.env['stock.picking'])
        picking_form.picking_type_id = self.env.ref('stock.picking_type_in')
        picking_form.partner_id = self.subcontractor_partner1
        with picking_form.move_ids_without_package.new() as move:
            move.product_id = self.finished
            move.product_uom_qty = 1
        picking_receipt1 = picking_form.save()
        picking_receipt1.action_confirm()

        # Create a receipt picking from the subcontractor2
        picking_form = Form(self.env['stock.picking'])
        picking_form.picking_type_id = self.env.ref('stock.picking_type_in')
        picking_form.partner_id = subcontractor_partner2
        with picking_form.move_ids_without_package.new() as move:
            move.product_id = self.finished
            move.product_uom_qty = 1
        picking_receipt2 = picking_form.save()
        picking_receipt2.action_confirm()

        mo_pick1 = picking_receipt1.move_lines.mapped('move_orig_ids.production_id')
        mo_pick2 = picking_receipt2.move_lines.mapped('move_orig_ids.production_id')
        self.assertEquals(len(mo_pick1), 1)
        self.assertEquals(len(mo_pick2), 1)
        self.assertEquals(mo_pick1.bom_id, self.bom)
        self.assertEquals(mo_pick2.bom_id, bom2)

    def test_flow_5(self):
        """ Extra quantity on the move.
        """
        # We create a second partner of type subcontractor
        main_partner_2 = self.env['res.partner'].create({'name': 'main_partner'})
        subcontractor_partner2 = self.env['res.partner'].create({
            'name': 'subcontractor_partner',
            'parent_id': main_partner_2.id,
        })
        self.env.cache.invalidate()

        # We create a different BoM for the same product
        comp3 = self.env['product.product'].create({
            'name': 'Component3',
            'type': 'product',
            'categ_id': self.env.ref('product.product_category_all').id,
        })

        bom_form = Form(self.env['mrp.bom'])
        bom_form.type = 'subcontract'
        bom_form.product_tmpl_id = self.finished.product_tmpl_id
        with bom_form.bom_line_ids.new() as bom_line:
            bom_line.product_id = self.comp1
            bom_line.product_qty = 1
        with bom_form.bom_line_ids.new() as bom_line:
            bom_line.product_id = comp3
            bom_line.product_qty = 2
        bom2 = bom_form.save()

        # We assign the second BoM to the new partner
        self.bom.write({'subcontractor_ids': [(4, self.subcontractor_partner1.id, None)]})
        bom2.write({'subcontractor_ids': [(4, subcontractor_partner2.id, None)]})

        # Create a receipt picking from the subcontractor1
        picking_form = Form(self.env['stock.picking'])
        picking_form.picking_type_id = self.env.ref('stock.picking_type_in')
        picking_form.partner_id = subcontractor_partner2
        with picking_form.move_ids_without_package.new() as move:
            move.product_id = self.finished
            move.product_uom_qty = 1
        picking_receipt = picking_form.save()
        picking_receipt.action_confirm()

        picking_receipt.move_lines.quantity_done = 3.0
        picking_receipt.action_done()
        mo = picking_receipt._get_subcontracted_productions()
        move_comp1 = mo.move_raw_ids.filtered(lambda m: m.product_id == self.comp1)
        move_comp3 = mo.move_raw_ids.filtered(lambda m: m.product_id == comp3)
        self.assertEqual(sum(move_comp1.mapped('product_uom_qty')), 3.0)
        self.assertEqual(sum(move_comp3.mapped('product_uom_qty')), 6.0)
        self.assertEqual(sum(move_comp1.mapped('quantity_done')), 3.0)
        self.assertEqual(sum(move_comp3.mapped('quantity_done')), 6.0)
        move_finished = mo.move_finished_ids
        self.assertEqual(sum(move_finished.mapped('product_uom_qty')), 3.0)
        self.assertEqual(sum(move_finished.mapped('quantity_done')), 3.0)


class TestSubcontractingTracking(TransactionCase):
    def setUp(self):
        super(TestSubcontractingTracking, self).setUp()
        # 1: Create a subcontracting partner
        main_company_1 = self.env['res.partner'].create({'name': 'main_partner'})
        self.subcontractor_partner1 = self.env['res.partner'].create({
            'name': 'Subcontractor 1',
            'parent_id': main_company_1.id,
            'company_id': self.env.ref('base.main_company').id
        })

        # 2. Create a BOM of subcontracting type
        # 2.1. Comp1 has tracking by lot
        self.comp1_sn = self.env['product.product'].create({
            'name': 'Component1',
            'type': 'product',
            'categ_id': self.env.ref('product.product_category_all').id,
            'tracking': 'serial'
        })
        self.comp2 = self.env['product.product'].create({
            'name': 'Component2',
            'type': 'product',
            'categ_id': self.env.ref('product.product_category_all').id,
        })

        # 2.2. Finished prodcut has tracking by serial number
        self.finished_lot = self.env['product.product'].create({
            'name': 'finished',
            'type': 'product',
            'categ_id': self.env.ref('product.product_category_all').id,
            'tracking': 'lot'
        })
        bom_form = Form(self.env['mrp.bom'])
        bom_form.type = 'subcontract'
        bom_form.product_tmpl_id = self.finished_lot.product_tmpl_id
        with bom_form.bom_line_ids.new() as bom_line:
            bom_line.product_id = self.comp1_sn
            bom_line.product_qty = 1
        with bom_form.bom_line_ids.new() as bom_line:
            bom_line.product_id = self.comp2
            bom_line.product_qty = 1
        self.bom_tracked = bom_form.save()

    def test_flow_tracked_1(self):
        """ This test mimics test_flow_1 but with a BoM that has tracking included in it.
        """
        # Create a receipt picking from the subcontractor
        picking_form = Form(self.env['stock.picking'])
        picking_form.picking_type_id = self.env.ref('stock.picking_type_in')
        picking_form.partner_id = self.subcontractor_partner1
        with picking_form.move_ids_without_package.new() as move:
            move.product_id = self.finished_lot
            move.product_uom_qty = 1
        picking_receipt = picking_form.save()
        picking_receipt.action_confirm()

        # We should be able to call the 'record_components' button
        self.assertTrue(picking_receipt.display_action_record_components)

        # Check the created manufacturing order
        mo = self.env['mrp.production'].search([('bom_id', '=', self.bom_tracked.id)])
        self.assertEqual(len(mo), 1)
        self.assertEquals(mo.state, 'confirmed')
        self.assertEqual(len(mo.picking_ids), 0)
        wh = picking_receipt.picking_type_id.warehouse_id
        self.assertEquals(mo.picking_type_id, wh.subcontracting_type_id)
        self.assertFalse(mo.picking_type_id.active)

        # Create a RR
        pg1 = self.env['procurement.group'].create({})
        self.env['stock.warehouse.orderpoint'].create({
            'name': 'xxx',
            'product_id': self.comp1_sn.id,
            'product_min_qty': 0,
            'product_max_qty': 0,
            'location_id': self.env.user.company_id.subcontracting_location_id.id,
            'group_id': pg1.id,
        })

        # Run the scheduler and check the created picking
        self.env['procurement.group'].run_scheduler()
        picking = self.env['stock.picking'].search([('group_id', '=', pg1.id)])
        self.assertEqual(len(picking), 1)
        self.assertEquals(picking.picking_type_id, wh.out_type_id)

        lot_id = self.env['stock.production.lot'].create({
            'name': 'lot1',
            'product_id': self.finished_lot.id
        })
        serial_id = self.env['stock.production.lot'].create({
            'name': 'lot1',
            'product_id': self.comp1_sn.id
        })
        produce_form = Form(self.env['mrp.product.produce'].with_context({
            'active_id': mo.id,
            'active_ids': [mo.id],
        }))
        produce_form.finished_lot_id = lot_id
        produce_form.raw_workorder_line_ids._records[0]['lot_id'] = serial_id.id
        wiz_produce = produce_form.save()
        wiz_produce.do_produce()

        # We should not be able to call the 'record_components' button
        self.assertFalse(picking_receipt.display_action_record_components)

        picking_receipt.move_lines.quantity_done = 1
        picking_receipt.move_lines.move_line_ids.lot_name = 'lot00001test'
        picking_receipt.button_validate()
        self.assertEquals(mo.state, 'done')

        # Available quantities should be negative at the subcontracting location for each components
        avail_qty_comp1 = self.env['stock.quant']._get_available_quantity(self.comp1_sn, self.subcontractor_partner1.property_stock_subcontractor, allow_negative=True)
        avail_qty_comp2 = self.env['stock.quant']._get_available_quantity(self.comp2, self.subcontractor_partner1.property_stock_subcontractor, allow_negative=True)
        avail_qty_finished = self.env['stock.quant']._get_available_quantity(self.finished_lot, wh.lot_stock_id)
        self.assertEquals(avail_qty_comp1, -1)
        self.assertEquals(avail_qty_comp2, -1)
        self.assertEquals(avail_qty_finished, 1)
