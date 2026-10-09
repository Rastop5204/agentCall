document.addEventListener("DOMContentLoaded", function () {
  var d = document.querySelector(".local-service");
  if (d) { d.setAttribute("open", ""); document.querySelector(".sidebar").classList.add("service-open"); }
});
