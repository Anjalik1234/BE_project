package com.codepulse.demo.model;

public class Order {
    private final String orderId;
    private final String productId;
    private final int quantity;
    private final double amount;

    public Order(String orderId, String productId, int quantity, double amount) {
        this.orderId = orderId;
        this.productId = productId;
        this.quantity = quantity;
        this.amount = amount;
    }

    public String getOrderId() { return orderId; }
    public String getProductId() { return productId; }
    public int getQuantity() { return quantity; }
    public double getAmount() { return amount; }
}
