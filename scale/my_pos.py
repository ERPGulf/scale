from erpnext.accounts.doctype.pos_invoice.pos_invoice import get_stock_availability
from erpnext.selling.page.point_of_sale.point_of_sale import get_conditions, get_item_group_condition, search_for_serial_or_batch_or_barcode_number
import frappe
from frappe.utils.nestedset import get_root_of

import json
from erpnext.accounts.doctype.pricing_rule.pricing_rule import get_pricing_rule_for_item
from erpnext.stock.get_item_details import get_basic_details, get_default_bom, get_gross_profit, get_item_tax_map, get_item_tax_template, get_party_item_code, get_pos_profile_item_details, get_price_list_currency_and_exchange_rate, get_price_list_rate, get_price_list_rate_for, insert_item_price, process_args, process_string_args, remove_standard_fields, set_valuation_rate, update_bin_details, update_party_blanket_order, validate_conversion_rate, validate_item_details
from frappe.utils.data import add_days, cint, flt



def searching_term(search_term, warehouse, price_list):
    scale_settings = frappe.get_single('Scale Settings')

    try:
        prefix_included = scale_settings.prefix_included_or_not
        prefix = scale_settings.prefix if prefix_included else ""
        prefix_length = int(scale_settings.no_of_prefix_characters) if prefix_included else 0
        item_code_start = int(scale_settings.item_code_starting_digit)
        item_code_length = int(scale_settings.item_code_total_digits)
        weight_start = int(scale_settings.weight_starting_digit)
        weight_length = int(scale_settings.weight_total_digits)
        weight_decimals = int(scale_settings.weight_decimals or 0)
        price_included = scale_settings.price_included_in_barcode_or_not
        price_start = int(scale_settings.price_starting_digit) if price_included else None
        price_length = int(scale_settings.price_total_digit) if price_included else None
        price_decimals = int(scale_settings.price_decimals or 0 ) if price_included else None
    except Exception as e:
        frappe.log_error(f"Error in fetching scale settings: {str(e)}")
        return

    qty = 0
    price_list_rate = 0

    try:
        if not prefix_included or (prefix_included and search_term.startswith(prefix)):
            barcode = search_term

            item_code_index = item_code_start - 1
            qty_index = weight_start - 1
            price_index = price_start - 1 if price_included else None

            if item_code_index is not None:
                item_code = barcode[item_code_index:item_code_index + item_code_length]
            if qty_index is not None:
                qty_str = barcode[qty_index:qty_index + weight_length]
                if weight_decimals > 0:
                    qty_str += "." + barcode[qty_index + weight_length:qty_index + weight_length + weight_decimals]
                qty = float(qty_str)
            if price_included and price_index is not None:
                price_str = barcode[price_index:price_index + price_length]
                if price_decimals > 0:
                    price_str += "." + barcode[price_index + price_length:price_index + price_length + price_decimals]
                price_list_rate = float(price_str)

            result = search_for_serial_or_batch_or_barcode_number(item_code) or {}
        else:
            result = search_for_serial_or_batch_or_barcode_number(search_term) or {}
    except Exception as e:
        frappe.log_error(f"Error in processing barcode: {str(e)}")
        return

    item_code = result.get("item_code", search_term)
    serial_no = result.get("serial_no", "")
    batch_no = result.get("batch_no", "")
    barcode = result.get("barcode", "")

    if not result:
        return

    item_doc = frappe.get_doc("Item", item_code)

    if not item_doc:
        return

    item = {
        "barcode": barcode,
        "batch_no": batch_no,
        "description": item_doc.description,
        "is_stock_item": item_doc.is_stock_item,
        "item_code": item_doc.name,
        "item_image": item_doc.image,
        "item_name": item_doc.item_name,
        "serial_no": serial_no,
        "stock_uom": item_doc.stock_uom,
        "uom": item_doc.stock_uom,
    }

    if barcode:
        barcode_info = next(filter(lambda x: x.barcode == barcode, item_doc.get("barcodes", [])), None)
        if barcode_info and barcode_info.uom:
            uom = next(filter(lambda x: x.uom == barcode_info.uom, item_doc.uoms), {})
            item.update(
                {
                    "uom": barcode_info.uom,
                    "conversion_factor": uom.get("conversion_factor", 1),
                }
            )

    item_stock_qty, is_stock_item = get_stock_availability(item_code, warehouse)
    item_stock_qty = item_stock_qty // item.get("conversion_factor", 1)
    item.update({"actual_qty": item_stock_qty})

    if not price_included:
        price = frappe.get_list(
            doctype="Item Price",
            filters={
                "price_list": price_list,
                "item_code": item_code,
                "batch_no": batch_no,
            },
            fields=["uom", "currency", "price_list_rate", "batch_no"],
        )

        def __sort(p):
            p_uom = p.get("uom")

            if p_uom == item.get("uom"):
                return 0
            elif p_uom == item.get("stock_uom"):
                return 1
            else:
                return 2

        price = sorted(price, key=__sort)

        if len(price) > 0:
            p = price.pop(0)
            item.update(
                {
                    "currency": p.get("currency"),
                    "price_list_rate": p.get("price_list_rate"),
                }
            )
    else:
        item.update({"price_list_rate": price_list_rate})

    item.update({"qty": qty})

    return {"items": [item]}


@frappe.whitelist()
def list_items(start, page_length, price_list, item_group, pos_profile, search_term=""):
    frappe.cache.set_value("search_term_" + frappe.session.user, search_term)

    warehouse, hide_unavailable_items = frappe.db.get_value(
        "POS Profile", pos_profile, ["warehouse", "hide_unavailable_items"]
    )

    result = []

    if search_term:
        result = searching_term(search_term, warehouse, price_list) or []
        if result:
            return result

    if not frappe.db.exists("Item Group", item_group):
        item_group = get_root_of("Item Group")

    condition = get_conditions(search_term)
    condition += get_item_group_condition(pos_profile)

    lft, rgt = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"])

    bin_join_selection, bin_join_condition = "", ""
    if hide_unavailable_items:
        bin_join_selection = ", `tabBin` bin"
        bin_join_condition = (
            "AND bin.warehouse = %(warehouse)s AND bin.item_code = item.name AND bin.actual_qty > 0"
        )

    items_data = frappe.db.sql(
        """
        SELECT
            item.name AS item_code,
            item.item_name,
            item.description,
            item.stock_uom,
            item.image AS item_image,
            item.is_stock_item
        FROM
            `tabItem` item {bin_join_selection}
        WHERE
            item.disabled = 0
            AND item.has_variants = 0
            AND item.is_sales_item = 1
            AND item.is_fixed_asset = 0
            AND item.item_group in (SELECT name FROM `tabItem Group` WHERE lft >= {lft} AND rgt <= {rgt})
            AND {condition}
            {bin_join_condition}
        ORDER BY
            item.name asc
        LIMIT
            {page_length} offset {start}""".format(
            start=cint(start),
            page_length=cint(page_length),
            lft=cint(lft),
            rgt=cint(rgt),
            condition=condition,
            bin_join_selection=bin_join_selection,
            bin_join_condition=bin_join_condition,
        ),
        {"warehouse": warehouse},
        as_dict=1,
    )

    # return (empty) list if there are no results
    if not items_data:
        return result

    for item in items_data:
        uoms = frappe.get_doc("Item", item.item_code).get("uoms", [])

        item.actual_qty, _ = get_stock_availability(item.item_code, warehouse)
        item.uom = item.stock_uom

        item_price = frappe.get_all(
            "Item Price",
            fields=["price_list_rate", "currency", "uom", "batch_no"],
            filters={
                "price_list": price_list,
                "item_code": item.item_code,
                "selling": True,
            },
        )

        if not item_price:
            result.append(item)

        for price in item_price:
            uom = next(filter(lambda x: x.uom == price.uom, uoms), {})

            if price.uom != item.stock_uom and uom and uom.conversion_factor:
                item.actual_qty = item.actual_qty // uom.conversion_factor

            result.append(
                {
                    **item,
                    "price_list_rate": price.get("price_list_rate"),
                    "currency": price.get("currency"),
                    "uom": price.uom or item.uom,
                    "batch_no": price.batch_no,
                }
            )
    return {"items": result}







@frappe.whitelist()
def list_item_details(args, doc=None, for_validate=False, overwrite_warehouse=True):
    """
    args = {
            "item_code": "",
            "warehouse": None,
            "customer": "",
            "conversion_rate": 1.0,
            "selling_price_list": None,
            "price_list_currency": None,
            "plc_conversion_rate": 1.0,
            "doctype": "",
            "name": "",
            "supplier": None,
            "transaction_date": None,
            "conversion_rate": 1.0,
            "buying_price_list": None,
            "is_subcontracted": 0/1,
            "ignore_pricing_rule": 0/1,
            "project": "",
            "set_warehouse": ""
    }
    """

    args = process_args(args)
    for_validate = process_string_args(for_validate)
    overwrite_warehouse = process_string_args(overwrite_warehouse)
    item = frappe.get_cached_doc("Item", args.item_code)
    validate_item_details(args, item)

    if isinstance(doc, str):
        doc = json.loads(doc)

    if doc:
        args["transaction_date"] = doc.get("transaction_date") or doc.get("posting_date")

        if doc.get("doctype") == "Purchase Invoice":
            args["bill_date"] = doc.get("bill_date")

    out = get_basic_details(args, item, overwrite_warehouse)

    get_item_tax_template(args, item, out)
    out["item_tax_rate"] = get_item_tax_map(
        args.company,
        args.get("item_tax_template")
        if out.get("item_tax_template") is None
        else out.get("item_tax_template"),
        as_json=True,
    )

    get_party_item_code(args, item, out)

    if args.get("doctype") in ["Sales Order", "Quotation"]:
        set_valuation_rate(out, args)

    update_party_blanket_order(args, out)

    # Never try to find a customer price if customer is set in these Doctype
    current_customer = args.customer
    if args.get("doctype") in ["Purchase Order", "Purchase Receipt", "Purchase Invoice"]:
        args.customer = None

    out.update(get_price_list_rate(args, item))

    args.customer = current_customer

    if args.customer and cint(args.is_pos):
        out.update(get_pos_profile_item_details(args.company, args, update_data=True))

    if item.is_stock_item:
        update_bin_details(args, out, doc)

    # update args with out, if key or value not exists
    for key, value in out.items():
        if args.get(key) is None:
            args[key] = value

    data = get_pricing_rule_for_item(args, doc=doc, for_validate=for_validate)

    out.update(data)

    if args.transaction_date and item.lead_time_days:
        out.schedule_date = out.lead_time_date = add_days(args.transaction_date, item.lead_time_days)

    if args.get("is_subcontracted"):
        out.bom = args.get("bom") or get_default_bom(args.item_code)

    get_gross_profit(out)
    if args.doctype == "Material Request":
        out.rate = args.rate or out.price_list_rate
        out.amount = flt(args.qty) * flt(out.rate)

    out = remove_standard_fields(out)

    scale_settings = frappe.get_single('Scale Settings')

    prefix_included = cint(scale_settings.prefix_included_or_not) if scale_settings.prefix_included_or_not else 0
    prefix = scale_settings.prefix if scale_settings.prefix else ""
    prefix_length = int(scale_settings.no_of_prefix_characters) if prefix_included else 0
    item_code_start = int(scale_settings.item_code_starting_digit) if scale_settings.item_code_starting_digit else 1
    item_code_length = int(scale_settings.item_code_total_digits) if scale_settings.item_code_total_digits else 0
    weight_start = int(scale_settings.weight_starting_digit) if scale_settings.weight_starting_digit else 1
    weight_length = int(scale_settings.weight_total_digits) if scale_settings.weight_total_digits else 0
    price_start = int(scale_settings.price_starting_digit) if scale_settings.price_starting_digit else None
    price_length = int(scale_settings.price_total_digit) if scale_settings.price_total_digit else 0
    weight_decimals = int(scale_settings.weight_decimals) if scale_settings.weight_decimals else 0
    price_decimals = int(scale_settings.price_decimals) if scale_settings.price_decimals else 0
    price_included = cint(scale_settings.price_included_in_barcode_or_not) if scale_settings.price_included_in_barcode_or_not else 0

    try:
        search_term = frappe.cache.get_value("search_term_" + frappe.session.user)
        if search_term:
            if not prefix_included or (prefix_included and search_term.startswith(prefix)):
                barcode = search_term

                item_code_index = item_code_start - 1
                qty_index = weight_start - 1
                price_index = price_start - 1 if price_included else None

                if item_code_index is not None:
                    item_code = barcode[item_code_index:item_code_index + item_code_length]
                if qty_index is not None:
                    qty_str = barcode[qty_index:qty_index + weight_length]
                    if weight_decimals > 0:
                        qty_str += "." + barcode[qty_index + weight_length:qty_index + weight_length + weight_decimals]
                    qty = float(qty_str)
                if price_included and price_index is not None:
                    price_str = barcode[price_index:price_index + price_length]
                    if price_decimals > 0:
                        price_str += "." + barcode[price_index + price_length:price_index + price_length + price_decimals]
                    price_list_rate = float(price_str)

                out.update({
                    "item_code": item_code,
                    "qty": qty,
                    "price_list_rate": price_list_rate
                })

    except Exception as e:
        frappe.log_error(f"Error in processing barcode: {str(e)}")

    if not price_included:
        result = searching_term(search_term, args.warehouse, args.price_list)
        if result and "items" in result:
            item_data = result["items"][0]
            out.update({
                "item_code": item_data.get("item_code"),
                "qty": item_data.get("qty"),
                "price_list_rate": item_data.get("price_list_rate")
            })

    return out


def list_price(args, item_doc, out=None):
    
    if out is None:
        out = frappe._dict()

    if item_doc is None:  
        return out 

    meta = frappe.get_meta(args.parenttype or args.doctype)

    if meta.get_field("currency") or args.get("currency"):
        if not args.get("price_list_currency") or not args.get("plc_conversion_rate"):
            # if currency and plc_conversion_rate exist then
            # `get_price_list_currency_and_exchange_rate` has already been called
            pl_details = get_price_list_currency_and_exchange_rate(args)
            args.update(pl_details)

        if meta.get_field("currency"):
            validate_conversion_rate(args, meta)

        price_list_rate = get_price_list_rate_for(args, item_doc.name)

        # variant
        if price_list_rate is None and item_doc.variant_of:
            price_list_rate = get_price_list_rate_for(args, item_doc.variant_of)

        # insert in database
        if price_list_rate is None or frappe.db.get_single_value(
            "Stock Settings", "update_existing_price_list_rate"
        ):
            if args.price_list and args.rate:
                insert_item_price(args)

            if not price_list_rate:
                return out

        out.price_list_rate = flt(price_list_rate) * flt(args.plc_conversion_rate) / flt(args.conversion_rate)

        if frappe.db.get_single_value("Buying Settings", "disable_last_purchase_rate"):
            return out

        if not out.price_list_rate and args.transaction_type == "buying":
            from erpnext.stock.doctype.item.item import get_last_purchase_details

            out.update(get_last_purchase_details(item_doc.name, args.name, args.conversion_rate))

    return out

