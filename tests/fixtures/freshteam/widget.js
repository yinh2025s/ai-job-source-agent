(function(elem) {
  function loadjsfile(filename) {
    var fileref = document.createElement("script");
    fileref.onload = function() {
      var jobWidget = new freshTeam.JobWidget(elem, "https://fixtureco.freshteam.com");
    };
    fileref.setAttribute("src", filename);
  }
  loadjsfile("https://assets1.freshteam.com/assets/job_widget.js");
})("freshteam-widget");
