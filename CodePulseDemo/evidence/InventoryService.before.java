package com.codepulse.demo.service;

import com.codepulse.demo.model.Order;

public class InventoryService {
    public boolean hasStock(Order order) {
        return order.getQuantity() > 0;
    }

    public void reduceStock(Order order) {
        System.out.println("Stock reduced for " + order.getProductId());
    }
}
