function overridePOSController() {
  if (
    typeof erpnext !== "undefined" &&
    typeof erpnext.PointOfSale !== "undefined" &&
    typeof erpnext.PointOfSale.Controller !== "undefined"
  ) {
    // Save the original on_cart_update method
    const original_on_cart_update =
      erpnext.PointOfSale.Controller.prototype.on_cart_update;

    // Override the on_cart_update method
    erpnext.PointOfSale.Controller.prototype.on_cart_update = async function (
      args
    ) {
      console.log("from controller");
      frappe.dom.freeze();
      let item_row = undefined;
      try {
        let { field, value, item } = args;
        item_row = this.get_item_from_frm(item);
        const item_row_exists = !$.isEmptyObject(item_row);

        const from_selector = field === "qty" && typeof value === "number";
        if (from_selector) {
          const response = await frappe.call({
            method: "scale.my_pos.get_test_qty",
          });
          value = flt(item_row.stock_qty) + flt(response.message.qty);
        }

        if (item_row_exists) {
          if (field === "qty") value = flt(value);

          if (
            ["qty", "conversion_factor"].includes(field) &&
            value > 0 &&
            !this.allow_negative_stock
          ) {
            const qty_needed =
              field === "qty"
                ? value * item_row.conversion_factor
                : item_row.qty * value;
            await this.check_stock_availability(
              item_row,
              qty_needed,
              this.frm.doc.set_warehouse
            );
          }

          if (this.is_current_item_being_edited(item_row) || from_selector) {
            await frappe.model.set_value(
              item_row.doctype,
              item_row.name,
              field,
              value
            );
            this.update_cart_html(item_row);
          }
        } else {
          if (!this.frm.doc.customer)
            return this.raise_customer_selection_alert();

          const { item_code, batch_no, serial_no, rate, uom } = item;

          if (!item_code) return;

          if (rate == undefined || rate == 0) {
            frappe.show_alert({
              message: __("Price is not set for the item."),
              indicator: "orange",
            });
            frappe.utils.play_sound("error");
            return;
          }
          const new_item = { item_code, batch_no, rate, uom, [field]: value };

          if (serial_no) {
            await this.check_serial_no_availablilty(
              item_code,
              this.frm.doc.set_warehouse,
              serial_no
            );
            new_item["serial_no"] = serial_no;
          }

          if (field === "serial_no")
            new_item["qty"] = value.split(`\n`).length || 0;

          item_row = this.frm.add_child("items", new_item);

          if (field === "qty" && value !== 0 && !this.allow_negative_stock) {
            const qty_needed = value * item_row.conversion_factor;
            await this.check_stock_availability(
              item_row,
              qty_needed,
              this.frm.doc.set_warehouse
            );
          }

          await this.trigger_new_item_events(item_row);

          this.update_cart_html(item_row);

          if (this.item_details.$component.is(":visible"))
            this.edit_item_details_of(item_row);

          if (
            this.check_serial_batch_selection_needed(item_row) &&
            !this.item_details.$component.is(":visible")
          )
            this.edit_item_details_of(item_row);
        }
      } catch (error) {
        console.log(error);
      } finally {
        frappe.dom.unfreeze();
        return item_row; // eslint-disable-line no-unsafe-finally
      }
    };
  } else {
    // Retry after 100ms if the class is not yet defined
    setTimeout(overridePOSController, 100);
  }
}
// Initial call to start the override process
overridePOSController();
