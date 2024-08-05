function overridePOSItemSelectorEvents() {
  if (
    typeof erpnext !== "undefined" &&
    typeof erpnext.PointOfSale !== "undefined" &&
    typeof erpnext.PointOfSale.ItemSelector !== "undefined"
  ) {
    const original_bind_events =
      erpnext.PointOfSale.ItemSelector.prototype.bind_events;

    erpnext.PointOfSale.ItemSelector.prototype.bind_events = function () {
      const me = this;
      window.onScan = onScan;
      console.log("from item selector");

      onScan.decodeKeyEvent = function (oEvent) {
        var iCode = this._getNormalizedKeyNum(oEvent);
        switch (true) {
          case iCode >= 48 && iCode <= 90: // numbers and letters
          case iCode >= 106 && iCode <= 111: // operations on numeric keypad (+, -, etc.)
          case (iCode >= 160 && iCode <= 164) || iCode == 170: // ^ ! # $ *
          case iCode >= 186 && iCode <= 194: // (; = , - . / )
          case iCode >= 219 && iCode <= 222: // ([ \ ] ')
          case iCode == 32: // spacebar
            if (oEvent.key !== undefined && oEvent.key !== "") {
              return oEvent.key;
            }

            var sDecoded = String.fromCharCode(iCode);
            switch (oEvent.shiftKey) {
              case false:
                sDecoded = sDecoded.toLowerCase();
                break;
              case true:
                sDecoded = sDecoded.toUpperCase();
                break;
            }
            return sDecoded;
          case iCode >= 96 && iCode <= 105: // numbers on numeric keypad
            return 0 + (iCode - 96);
        }
        return "";
      };

      onScan.attachTo(document, {
        onScan: (sScancode) => {
          if (this.search_field && this.$component.is(":visible")) {
            this.search_field.set_focus();
            this.set_search_value(sScancode);
            this.barcode_scanned = true;
          }
        },
      });

      this.$component.on("click", ".item-wrapper", function () {
        const $item = $(this);
        const item_code = unescape($item.attr("data-item-code"));
        let batch_no = unescape($item.attr("data-batch-no"));
        let serial_no = unescape($item.attr("data-serial-no"));
        let uom = unescape($item.attr("data-uom"));
        let rate = unescape($item.attr("data-rate"));

        // escape(undefined) returns "undefined" then unescape returns "undefined"
        batch_no = batch_no === "undefined" ? undefined : batch_no;
        serial_no = serial_no === "undefined" ? undefined : serial_no;
        uom = uom === "undefined" ? undefined : uom;
        rate = rate === "undefined" ? undefined : rate;

        frappe.call({
          method: "scale.my_pos.get_test_qty",
          callback: function (response) {
            const qty = response.message.qty;

            me.events.item_selected({
              field: "qty",
              value: qty,
              item: { item_code, batch_no, serial_no, uom, rate },
            });
          },
        });

        me.search_field.set_focus();
      });

      this.search_field.$input.on("input", (e) => {
        clearTimeout(this.last_search);
        this.last_search = setTimeout(() => {
          const search_term = e.target.value;
          this.filter_items({ search_term });
        }, 300);

        this.$clear_search_btn.toggle(Boolean(this.search_field.$input.val()));
      });

      this.search_field.$input.on("focus", () => {
        this.$clear_search_btn.toggle(Boolean(this.search_field.$input.val()));
      });
    };
  } else {
    setTimeout(overridePOSItemSelectorEvents, 200);
  }
}
overridePOSItemSelectorEvents();
