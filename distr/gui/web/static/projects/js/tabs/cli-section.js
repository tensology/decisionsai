(function() {
    window.ProjectsTabSections = window.ProjectsTabSections || {};
    window.ProjectsTabSections.cli = {
        onActivated: function() {
            setTimeout(function() {
                var input = document.getElementById("terminal-input");
                if (input) input.focus();
            }, 0);
        }
    };
})();
