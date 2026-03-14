// document.addEventListener("DOMContentLoaded", function () {

//     const form = document.querySelector("form");

//     form.addEventListener("submit", function (event) {
//         event.preventDefault(); // stop form submit

//         const password = document.querySelector("input[name='password']").value;

//         // 🔐 Password Regex
//         const passwordRegex =
//             /^(?=.*[A-Z])(?=.*[@$!%*?&#])[A-Za-z\d@$!%*?&#]{8,}$/;

//         if (!passwordRegex.test(password)) {
//             alert(
//                 "Password must be at least 8 characters long and include:\n" +
//                 "• One CAPITAL letter\n" +
//                 "• One special character"
//             );
//             return;
//         }

//         // If password is valid → submit form
//         form.submit();
//     });

// });


// register.js

function validatePasswordOnSubmit() {
    const password = document.getElementById("password").value;
    const missing = [];

    // If empty
    if (!password) {
        alert("Password is required.");
        return false; // prevent submission
    }

    // Uppercase check
    if (!/[A-Z]/.test(password)) missing.push("at least 1 uppercase letter");

    // Number check
    if (!/\d/.test(password)) missing.push("at least 1 number");

    // Special character check
    if (!/[@$!%*?&]/.test(password)) missing.push("at least 1 special character (@, $, !, %, *, ?, &)");

    // Minimum length check
    if (password.length < 8) missing.push("minimum 8 characters");

    if (missing.length > 0) {
        alert("Password must contain: " + missing.join(", "));
        return false; // prevent submission
    }

    return true; // all requirements met
}
