document.addEventListener('DOMContentLoaded', function() {
    // Handle approve product
    document.querySelectorAll('.approve-btn').forEach(function(button) {
        button.addEventListener('click', function() {
            var productId = button.getAttribute('data-product-id');
            approveProduct(productId);
        });
    });
        
    // Handle reject product
    document.querySelectorAll('.reject-btn').forEach(function(button) {
        button.addEventListener('click', function() {
            var productId = button.getAttribute('data-product-id');
            rejectProduct(productId);
        });
    });
        
    // Function to approve product via AJAX
    function approveProduct(productId) {
        fetch(`/approve_product/${productId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': '{{ csrf_token() }}'  // Ensure to pass CSRF token
            }
        })
        .then(response => {
            if (response.ok) {
                return response.json();
            }
            throw new Error('Network response was not ok.');
        })
        .then(data => {
            // Handle success, e.g., update UI or show a message
            console.log(data.message);
            window.location.reload();  // Reload the page after approval
        })
        .catch(error => {
            console.error('Error:', error);
            // Handle error, e.g., show an alert or error message
        });
    }
        
    // Function to reject product via AJAX
    function rejectProduct(productId) {
        fetch(`/reject_product/${productId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': '{{ csrf_token() }}'  // Ensure to pass CSRF token
            }
        })
        .then(response => {
            if (response.ok) {
                return response.json();
            }
            throw new Error('Network response was not ok.');
        })
        .then(data => {
            // Handle success, e.g., update UI or show a message
            console.log(data.message);
            window.location.reload();  // Reload the page after rejection
        })
        .catch(error => {
            console.error('Error:', error);
            // Handle error, e.g., show an alert or error message
        });
    }
});

$(document).ready(function() {
    $('#userTable').DataTable({
        dom: 'Bfrtip',
        buttons: [
            'copy', 'csv', 'excel', 'pdf', 'print'
        ]
    });
});


var ctx = document.getElementById('salesChart').getContext('2d');
var salesChart = new Chart(ctx, {
    type: 'bar',
    data: {
        labels: ['Total Products', 'Total Sales'],
        datasets: [{
            label: 'Sales Data',
            data: [{{ total_products }}, {{ total_sales }}],
            backgroundColor: ['rgba(75, 192, 192, 0.2)', 'rgba(153, 102, 255, 0.2)'],
            borderColor: ['rgba(75, 192, 192, 1)', 'rgba(153, 102, 255, 1)'],
            borderWidth: 1
        }]
    },
    options: {
        scales: {
            y: {
                beginAtZero: true
            }
        }
    }
});
