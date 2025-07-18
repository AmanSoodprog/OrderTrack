<?php
/**
 * Plugin Name: WooCommerce Guest Order Linker
 * Plugin URI: https://yourwebsite.com
 * Description: Links guest orders to customer accounts for WooCommerce and custom Google login.
 * Version: 1.0.0
 * Author: Your Name
 * License: GPL v2 or later
 * Text Domain: wc-guest-order-linker
 */

// Prevent direct access
if (!defined('ABSPATH')) {
    exit;
}

// Check if WooCommerce is active
if (!class_exists('WooCommerce')) {
    add_action('admin_notices', function() {
        echo '<div class="notice notice-error"><p>WooCommerce Guest Order Linker requires WooCommerce to be installed and active.</p></div>';
    });
    return;
}

class WC_Guest_Order_Linker {
    
    const VERSION = '1.0.0';
    
    public function __construct() {
        $this->init_hooks();
    }
    
    private function init_hooks() {
        // WooCommerce/WordPress login
        add_action('wp_login', array($this, 'link_guest_orders_on_login'), 10, 2);
        add_action('user_register', array($this, 'link_guest_orders_on_registration'));
        add_action('woocommerce_created_customer', array($this, 'link_guest_orders_on_checkout_registration'));
        
        // Custom hook for your Google login - you'll call this from your Google login code
        add_action('custom_google_login_success', array($this, 'link_guest_orders_google_login'), 10, 1);
        
        // Detect Google login redirects
        add_action('wp', array($this, 'detect_google_login'));
        
        // Fallback: Check when user visits any page after login
        add_action('wp', array($this, 'check_and_link_on_page_load'));
        
        // Admin interface
        add_action('admin_menu', array($this, 'add_admin_menu'));
        add_action('admin_enqueue_scripts', array($this, 'admin_scripts'));
        add_action('wp_ajax_wc_link_guest_orders', array($this, 'ajax_link_guest_orders'));
        add_action('wp_ajax_wc_get_link_stats', array($this, 'ajax_get_link_stats'));
        
        register_activation_hook(__FILE__, array($this, 'activate'));
    }
    
    /**
     * Link guest orders when user logs in via WooCommerce/WordPress
     */
    public function link_guest_orders_on_login($user_login, $user) {
        $this->link_orders_by_email($user->user_email, $user->ID);
    }
    
    /**
     * Link guest orders when user registers
     */
    public function link_guest_orders_on_registration($user_id) {
        $user = get_userdata($user_id);
        if ($user) {
            $this->link_orders_by_email($user->user_email, $user_id);
        }
    }
    
    /**
     * Link guest orders during WooCommerce checkout registration
     */
    public function link_guest_orders_on_checkout_registration($customer_id) {
        $user = get_userdata($customer_id);
        if ($user) {
            $this->link_orders_by_email($user->user_email, $customer_id);
        }
    }
    
    /**
     * Handle custom Google login - call this from your Google login code
     */
    public function link_guest_orders_google_login($user_id) {
        $user = get_userdata($user_id);
        if ($user) {
            $this->link_orders_by_email($user->user_email, $user_id);
        }
    }
    
    /**
     * Detect Google login and trigger order linking
     */
    public function detect_google_login() {
        // Check if this is a Google OAuth redirect
        if (!isset($_GET['code']) || !isset($_GET['state'])) {
            return;
        }
        
        // Check if user is logged in (your existing Google login handled it)
        if (!is_user_logged_in()) {
            return;
        }
        
        $user_id = get_current_user_id();
        
        // Check if we've already processed this Google login session
        $processed_key = 'google_login_processed_' . $user_id . '_' . substr($_GET['code'], 0, 10);
        $already_processed = get_transient($processed_key);
        
        if (!$already_processed) {
            $user = get_userdata($user_id);
            if ($user) {
                $linked_count = $this->link_orders_by_email($user->user_email, $user_id);
                
                if ($linked_count > 0) {
                    error_log("WC Guest Order Linker: Google login - Linked {$linked_count} orders for user {$user_id}");
                }
                
                // Mark as processed for this session (5 minutes)
                set_transient($processed_key, true, 300);
            }
        }
    }
    
    /**
     * Fallback: Check on page load if user logged in recently and hasn't been processed
     */
    public function check_and_link_on_page_load() {
        if (!is_user_logged_in()) {
            return;
        }
        
        $user_id = get_current_user_id();
        
        // Check if already processed recently (avoid multiple processing)
        $last_check = get_user_meta($user_id, '_wc_guest_orders_last_check', true);
        $current_time = current_time('timestamp');
        
        // Only check once per hour
        if (!empty($last_check) && ($current_time - $last_check) < 3600) {
            return;
        }
        
        $user = get_userdata($user_id);
        if ($user) {
            $linked_count = $this->link_orders_by_email($user->user_email, $user_id);
            update_user_meta($user_id, '_wc_guest_orders_last_check', $current_time);
            
            if ($linked_count > 0) {
                error_log("WC Guest Order Linker: Linked {$linked_count} orders for user {$user_id} on page load");
            }
        }
    }
    
    /**
     * Core function to link orders by email
     */
    private function link_orders_by_email($email, $user_id) {
        if (empty($email) || empty($user_id)) {
            return 0;
        }
        
        // Find guest orders with matching email
        $guest_orders = wc_get_orders(array(
            'billing_email' => $email,
            'customer_id' => 0, // Guest orders
            'limit' => -1,
            'status' => 'any'
        ));
        
        $linked_count = 0;
        
        foreach ($guest_orders as $order) {
            $order->set_customer_id($user_id);
            $order->save();
            $linked_count++;
        }
        
        if ($linked_count > 0) {
            error_log("WC Guest Order Linker: Linked {$linked_count} orders to user {$user_id} ({$email})");
        }
        
        return $linked_count;
    }
    
    /**
     * Add admin menu
     */
    public function add_admin_menu() {
        add_submenu_page(
            'woocommerce',
            'Guest Order Linker',
            'Guest Order Linker',
            'manage_woocommerce',
            'wc-guest-order-linker',
            array($this, 'admin_page')
        );
    }
    
    /**
     * Admin page
     */
    public function admin_page() {
        ?>
        <div class="wrap">
            <h1>WooCommerce Guest Order Linker</h1>
            
            <div class="card">
                <h2>Link Existing Guest Orders</h2>
                <p>Link all existing guest orders to customer accounts with matching email addresses.</p>
                
                <div id="link-stats" style="margin: 20px 0;">
                    <button type="button" id="get-stats" class="button">Get Statistics</button>
                    <div id="stats-results" style="margin-top: 10px;"></div>
                </div>
                
                <div id="link-actions">
                    <button type="button" id="link-orders" class="button button-primary">Link All Guest Orders</button>
                    <div id="link-results" style="margin-top: 10px;"></div>
                </div>
            </div>
            
            <div class="card" style="margin-top: 20px;">
                <h2>Setup Instructions</h2>
                <p>To integrate with your custom Google login, add this line to your Google login success code:</p>
                <code style="display: block; background: #f1f1f1; padding: 10px; margin: 10px 0;">
                do_action('custom_google_login_success', $user_id);
                </code>
                <p>Replace <code>$user_id</code> with the WordPress user ID of the logged-in user.</p>
                
                <h3>How It Works</h3>
                <ul>
                    <li><strong>WooCommerce Login:</strong> Automatically links orders when users log in through WooCommerce</li>
                    <li><strong>Google Login Detection:</strong> Automatically detects Google OAuth redirects and links orders</li>
                    <li><strong>Fallback:</strong> Checks and links orders when users visit any page (once per hour max)</li>
                    <li><strong>Manual Linking:</strong> Use the tool above for existing guest orders</li>
                </ul>
            </div>
        </div>
        <?php
    }
    
    /**
     * Admin scripts
     */
    public function admin_scripts($hook) {
        if ($hook !== 'woocommerce_page_wc-guest-order-linker') {
            return;
        }
        
        wp_enqueue_script('jquery');
        ?>
        <script type="text/javascript">
        jQuery(document).ready(function($) {
            $('#get-stats').on('click', function() {
                var button = $(this);
                button.prop('disabled', true).text('Loading...');
                
                $.ajax({
                    url: ajaxurl,
                    type: 'POST',
                    data: {
                        action: 'wc_get_link_stats',
                        nonce: '<?php echo wp_create_nonce('wc_guest_order_linker'); ?>'
                    },
                    success: function(response) {
                        if (response.success) {
                            $('#stats-results').html(
                                '<div class="notice notice-info inline">' +
                                '<p><strong>Statistics:</strong></p>' +
                                '<ul>' +
                                '<li>Guest orders that can be linked: ' + response.data.linkable_orders + '</li>' +
                                '<li>Total guest orders: ' + response.data.total_guest_orders + '</li>' +
                                '<li>Registered customers: ' + response.data.total_customers + '</li>' +
                                '</ul>' +
                                '</div>'
                            );
                        } else {
                            $('#stats-results').html('<div class="notice notice-error inline"><p>Error: ' + response.data + '</p></div>');
                        }
                    },
                    complete: function() {
                        button.prop('disabled', false).text('Get Statistics');
                    }
                });
            });
            
            $('#link-orders').on('click', function() {
                if (!confirm('Link all guest orders to matching customer accounts?')) {
                    return;
                }
                
                var button = $(this);
                button.prop('disabled', true).text('Processing...');
                
                $.ajax({
                    url: ajaxurl,
                    type: 'POST',
                    data: {
                        action: 'wc_link_guest_orders',
                        nonce: '<?php echo wp_create_nonce('wc_guest_order_linker'); ?>'
                    },
                    success: function(response) {
                        if (response.success) {
                            $('#link-results').html(
                                '<div class="notice notice-success inline">' +
                                '<p><strong>Success!</strong> Linked ' + response.data.linked_count + ' orders to ' + response.data.customers_updated + ' customers.</p>' +
                                '</div>'
                            );
                        } else {
                            $('#link-results').html('<div class="notice notice-error inline"><p>Error: ' + response.data + '</p></div>');
                        }
                    },
                    complete: function() {
                        button.prop('disabled', false).text('Link All Guest Orders');
                    }
                });
            });
        });
        </script>
        <?php
    }
    
    /**
     * AJAX: Get linking statistics
     */
    public function ajax_get_link_stats() {
        check_ajax_referer('wc_guest_order_linker', 'nonce');
        
        if (!current_user_can('manage_woocommerce')) {
            wp_die('Insufficient permissions');
        }
        
        global $wpdb;
        
        $total_guest_orders = $wpdb->get_var("
            SELECT COUNT(*) 
            FROM {$wpdb->posts} 
            WHERE post_type = 'shop_order' 
            AND post_author = 0
        ");
        
        $total_customers = $wpdb->get_var("
            SELECT COUNT(*) 
            FROM {$wpdb->users}
        ");
        
        $linkable_orders = $wpdb->get_var("
            SELECT COUNT(DISTINCT p.ID)
            FROM {$wpdb->posts} p
            INNER JOIN {$wpdb->postmeta} pm ON p.ID = pm.post_id
            INNER JOIN {$wpdb->users} u ON pm.meta_value = u.user_email
            WHERE p.post_type = 'shop_order'
            AND p.post_author = 0
            AND pm.meta_key = '_billing_email'
        ");
        
        wp_send_json_success(array(
            'total_guest_orders' => intval($total_guest_orders),
            'total_customers' => intval($total_customers),
            'linkable_orders' => intval($linkable_orders)
        ));
    }
    
    /**
     * AJAX: Link guest orders
     */
    public function ajax_link_guest_orders() {
        check_ajax_referer('wc_guest_order_linker', 'nonce');
        
        if (!current_user_can('manage_woocommerce')) {
            wp_die('Insufficient permissions');
        }
        
        $linked_count = 0;
        $customers_updated = 0;
        
        $users = get_users(array('fields' => array('ID', 'user_email')));
        
        foreach ($users as $user) {
            $user_linked = $this->link_orders_by_email($user->user_email, $user->ID);
            if ($user_linked > 0) {
                $linked_count += $user_linked;
                $customers_updated++;
            }
        }
        
        wp_send_json_success(array(
            'linked_count' => $linked_count,
            'customers_updated' => $customers_updated
        ));
    }
    
    public function activate() {
        add_option('wc_guest_order_linker_version', self::VERSION);
    }
}

// Initialize the plugin
new WC_Guest_Order_Linker();
?>