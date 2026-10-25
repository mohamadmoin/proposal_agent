// Show the "name of the design" field only when the design is "other".
(function () {
  "use strict";
  var select = document.getElementById("design");
  var field = document.getElementById("other-name-field");
  if (!select || !field) return;
  function update() {
    field.hidden = select.value !== "other";
  }
  select.addEventListener("change", update);
  update();
})();
